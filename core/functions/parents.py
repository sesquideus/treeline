"""
Slope and horizon parents by branch and bound, in place of comparing every summit with every
other one.

- Slope parent of s: the summit q maximising (h_q − h_s) / d(s, q), if q is higher.
- Horizon parent of s: the summit q of the greatest elevation angle seen from s over an Earth of
  radius R / (1 − k) (k: refraction), if that angle is positive. Only a higher q can give one.

The summits go into a vantage-point tree on their positions on the unit sphere, with the great-
circle angle as the metric (a true metric, so the triangle inequality bounds every subtree, with no
special case at the poles or the antimeridian). The tree is implicit: the summits' order is the
tree. A range [lo, hi) of it holds a vantage point at lo, the summits nearer to it than the median
in [lo + 1, mid) and the farther ones in [mid, hi); per vantage point the tree keeps where its
range splits, the ring of angles each half lies in, and each half's highest altitude. For a query
at angle a from the vantage point, a half lies no nearer than max(lo − a, a − hi, 0); nothing in
it can do better than its highest altitude at that distance, since both scores grow with the
target's height and fall with its distance. So a search per summit opens the nearer half first and
drops every half that cannot beat the best found so far. The search is compiled (numba); measured
on 263089 summits it takes ~3 µs a summit, against ~4 µs for a compiled k-d tree and ~110 µs for
the k-d tree this replaced, which was pure Python.

The answer is the same as the exhaustive search's: distances are first estimated on a sphere with
a known bound on the error, every candidate that the error could make the winner is kept, and
those few are decided by the exact geodesic (Karney's, through pyproj, all at once), as before.
"""
import math

import numpy as np
from numba import njit
from pyproj import Geod

EARTH = 6371008.8             # m, mean radius: the sphere distances are estimated on
# The geodesic on WGS84 and the great circle on EARTH differ by under 0.6 % at any distance;
# 0.7 % keeps every bound safe.
SLACK = 0.007
B_LOW = 6356752.3 * (1 - SLACK)  # m: a sphere no geodesic is shorter than, for the subtree bounds
LEAF = 8
GEOD = Geod(ellps='WGS84')
SLOPE, ANGLE = 0, 1           # the score, for the compiled search


def slope(hs, hq, d):
    return math.inf if d <= 0 else (hq - hs) / d


def angle(refraction):
    r = 6371000 / (1 - refraction)

    def score(hs, hq, d):
        if d <= 0:
            return math.inf
        beta = d / r
        if beta >= math.pi:
            beta = math.pi - 1e-12
        return math.atan(((r + hq) * math.cos(beta) - (r + hs)) / ((r + hq) * math.sin(beta)))
    score.radius = r
    return score


@njit(cache=True)
def _score(kind, r, hs, hq, d):
    if d <= 0:
        return np.inf
    if kind == SLOPE:
        return (hq - hs) / d
    beta = d / r
    if beta >= math.pi:
        beta = math.pi - 1e-12
    return math.atan(((r + hq) * math.cos(beta) - (r + hs)) / ((r + hq) * math.sin(beta)))


@njit(cache=True)
def _ang(X, i, j):
    """ The great-circle angle between unit vectors X[i] and X[j], by the chord (stable when small). """
    dx, dy, dz = X[i, 0] - X[j, 0], X[i, 1] - X[j, 1], X[i, 2] - X[j, 2]
    return 2 * math.asin(min(1.0, math.sqrt(dx * dx + dy * dy + dz * dz) / 2))


def _build(X, h, seed=0):
    """ The implicit tree: (order, split, ring, h_in, h_out), all indexed by a range's first position. """
    n = len(X)
    rng = np.random.default_rng(seed)
    order = np.arange(n)
    split = np.zeros(n, np.int64)
    ring = np.zeros((n, 4))                      # inner lo, hi, outer lo, hi: angles from the vantage point
    h_in = np.full(n, -np.inf)
    h_out = np.full(n, -np.inf)
    arc = lambda a, b: 2 * np.arcsin(np.minimum(1.0, np.linalg.norm(a - b, axis=-1) / 2))
    stack = [(0, n)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo <= LEAF:
            continue
        idx = order[lo:hi]
        # the vantage point: of a few candidates, the one whose distances spread most (an even split)
        best, v = -1.0, idx[0]
        for c in rng.choice(idx, size=min(5, len(idx)), replace=False):
            d = np.sort(arc(X[rng.choice(idx, size=min(40, len(idx)), replace=False)], X[c]))
            spread = d[len(d) * 3 // 4] - d[len(d) // 4]
            if spread > best:
                best, v = spread, c
        rest = idx[idx != v]
        d = arc(X[rest], X[v])
        o = np.argsort(d, kind='stable')
        rest, d = rest[o], d[o]
        order[lo], order[lo + 1:hi] = v, rest
        mid = lo + 1 + (hi - lo - 1) // 2
        a = mid - lo - 1
        split[lo] = mid
        ring[lo] = (d[0], d[a - 1], d[a], d[-1]) if a > 0 else (0.0, 0.0, d[0], d[-1])
        h_in[lo] = h[rest[:a]].max() if a > 0 else -np.inf
        h_out[lo] = h[rest[a:]].max()
        stack += [(lo + 1, mid), (mid, hi)]
    return order, split, ring, h_in, h_out


@njit(cache=True)
def _consider(X, h, i, j, kind, r, buf, n, floor):
    """ Item j as a candidate for i: (count, floor) after keeping it if it may win. """
    if j == i or h[j] <= h[i]:
        return n, floor
    d = EARTH * _ang(X, i, j)
    up = _score(kind, r, h[i], h[j], d * (1 - SLACK))
    if up <= floor:
        return n, floor
    low = _score(kind, r, h[i], h[j], d * (1 + SLACK))
    if n == len(buf):
        return -1, floor                     # the caller grows the buffer and asks again
    buf[n, 0] = up
    buf[n, 1] = j
    return n + 1, max(floor, low)


@njit(cache=True)
def _candidates(X, h, order, split, ring, h_in, h_out, i, kind, r, buf):
    """ The candidates for summit i: (count, floor) with buf[:count] = (upper bound, index); -1 if buf is too small. """
    total = len(X)
    stack = np.empty((128, 2), np.int64)
    stack[0, 0], stack[0, 1] = 0, total
    sp, n, floor = 1, 0, -np.inf
    hs = h[i]
    while sp:
        sp -= 1
        lo, hi = stack[sp, 0], stack[sp, 1]
        if hi - lo <= LEAF:
            for t in range(lo, hi):
                n, floor = _consider(X, h, i, order[t], kind, r, buf, n, floor)
                if n < 0:
                    return -1, floor
            continue
        v = order[lo]
        a = _ang(X, i, v)
        n, floor = _consider(X, h, i, v, kind, r, buf, n, floor)
        if n < 0:
            return -1, floor
        mid = split[lo]
        least_in = max(ring[lo, 0] - a, a - ring[lo, 1], 0.0) * B_LOW
        least_out = max(ring[lo, 2] - a, a - ring[lo, 3], 0.0) * B_LOW
        go_in = mid > lo + 1 and h_in[lo] > hs and _score(kind, r, hs, h_in[lo], least_in) > floor
        go_out = hi > mid and h_out[lo] > hs and _score(kind, r, hs, h_out[lo], least_out) > floor
        if sp + 2 > len(stack):
            bigger = np.empty((2 * len(stack), 2), np.int64)
            bigger[:sp] = stack[:sp]
            stack = bigger
        # the nearer half last, so that it is opened first
        if go_in and go_out:
            if least_in < least_out:
                stack[sp, 0], stack[sp, 1] = mid, hi
                stack[sp + 1, 0], stack[sp + 1, 1] = lo + 1, mid
            else:
                stack[sp, 0], stack[sp, 1] = lo + 1, mid
                stack[sp + 1, 0], stack[sp + 1, 1] = mid, hi
            sp += 2
        elif go_in:
            stack[sp, 0], stack[sp, 1] = lo + 1, mid
            sp += 1
        elif go_out:
            stack[sp, 0], stack[sp, 1] = mid, hi
            sp += 1
    return n, floor


@njit(cache=True)
def _search(X, h, order, split, ring, h_in, h_out, queries, kind, r, cap):
    """ For each query, its candidates still able to win: (starts, indices), CSR; cap: the first buffer size. """
    starts = np.empty(len(queries) + 1, np.int64)
    out = np.empty(max(16, 2 * len(queries)), np.int64)
    m = 0
    buf = np.empty((cap, 2))
    for q in range(len(queries)):
        starts[q] = m
        while True:
            n, floor = _candidates(X, h, order, split, ring, h_in, h_out, queries[q], kind, r, buf)
            if n >= 0:
                break
            buf = np.empty((2 * len(buf), 2))
        for c in range(n):
            if buf[c, 0] < floor:
                continue
            if m == len(out):
                bigger = np.empty(2 * len(out), np.int64)
                bigger[:m] = out[:m]
                out = bigger
            out[m] = np.int64(buf[c, 1])
            m += 1
    starts[len(queries)] = m
    return starts, out[:m]


def slope_parents(summits, keys=None, progress=None):
    """{key: (parent key or None, slope)} for `keys` (default: all) among `summits`."""
    return _parents(summits, keys, slope, SLOPE, 0.0, positive=False, progress=progress)


def horizon_parents(summits, keys=None, refraction=0.0, progress=None):
    """{key: (parent key or None, angle)}; a parent only where the angle is positive."""
    score = angle(refraction)
    return _parents(summits, keys, score, ANGLE, score.radius, positive=True, progress=progress)


def _parents(summits, keys, score, kind, r, positive, progress=None):
    """
    `summits`: [(key, lat, lon, altitude)], altitude None left out. `progress(done, total)`, if
    given, is called as the answers are decided (every 1000 summits) and at the end.
    """
    items = [(k, lat, lon, h) for k, lat, lon, h in summits if h is not None]
    out = {}
    if not items:
        return out
    lat = np.radians(np.array([it[1] for it in items], float))
    lon = np.radians(np.array([it[2] for it in items], float))
    X = np.stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=1)
    h = np.array([it[3] for it in items], float)
    wanted = None if keys is None else set(keys)
    queries = np.array([n for n, it in enumerate(items) if wanted is None or it[0] in wanted], np.int64)
    starts, cands = _search(X, h, *_build(X, h), queries, kind, r, 64)
    total = len(queries)
    # What the estimate could not tell apart is decided by the exact geodesic (Karney's, as before,
    # here in C through pyproj and for all candidates at once).
    owner = np.repeat(queries, np.diff(starts))
    deg = np.array([(it[1], it[2]) for it in items], float)
    _, _, d = GEOD.inv(deg[owner, 1], deg[owner, 0], deg[cands, 1], deg[cands, 0])
    hs, hq = h[owner], h[cands]
    with np.errstate(divide='ignore', invalid='ignore'):
        if kind == SLOPE:
            s = np.where(d > 0, (hq - hs) / d, np.inf)
        else:
            beta = np.minimum(d / r, math.pi - 1e-12)
            s = np.where(d > 0, np.arctan(((r + hq) * np.cos(beta) - (r + hs)) / ((r + hq) * np.sin(beta))), np.inf)
    for q, i in enumerate(queries):
        a, b = starts[q], starts[q + 1]
        if a == b:
            out[items[i][0]] = (None, None)
        else:
            k = a + int(np.argmax(s[a:b]))          # the first of equal bests, as the loop before took it
            best, best_score = items[cands[k]][0], float(s[k])
            out[items[i][0]] = (None, None) if positive and best_score <= 0 else (best, best_score)
        if progress and (q + 1) % 1000 == 0:
            progress(q + 1, total)
    if progress:
        progress(total, total)
    return out
