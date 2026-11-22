"""
Slope and horizon parents by branch and bound, in place of comparing every summit with every
other one.

- Slope parent of s: the summit q maximising (h_q − h_s) / d(s, q), if q is higher.
- Horizon parent of s: the summit q of the greatest elevation angle seen from s over an Earth of
  radius R / (1 − k) (k: refraction), if that angle is positive. Only a higher q can give one.

The summits go into a k-d tree on their positions on the unit sphere (no special case at the
poles or the antimeridian); each node keeps its box and the highest altitude in it. For a node,
nothing inside can do better than its highest altitude at the least distance the box allows —
both scores grow with the target's height and fall with its distance — so a search per summit
opens the nearest nodes first and drops every node that cannot beat the best found so far.
The answer is the same as the exhaustive search's: distances are first estimated on a sphere
with a known bound on the error, every candidate that the error could make the winner is kept,
and those few are decided by the exact geodesic (`core.functions.world.distance`), as before.
"""
import math

from geographiclib.geodesic import Geodesic

EARTH = 6371008.8             # m, mean radius: the sphere distances are estimated on
# The geodesic on WGS84 and the great circle on EARTH differ by under 0.6 % at any distance;
# 0.7 % keeps every bound safe.
SLACK = 0.007
B_LOW = 6356752.3 * (1 - SLACK)  # m: a sphere no geodesic is shorter than, for the node bounds
LEAF = 8


def _unit(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    c = math.cos(la)
    return c * math.cos(lo), c * math.sin(lo), math.sin(la)


class SummitTree:
    """`summits`: [(key, lat, lon, altitude)]; altitude None is left out."""

    def __init__(self, summits):
        self.items = [(k, lat, lon, h, *_unit(lat, lon)) for k, lat, lon, h in summits if h is not None]
        # nodes: [xmin, xmax, ymin, ymax, zmin, zmax, hmax, left, right, start, end]
        self.nodes = []
        self.order = list(range(len(self.items)))
        if self.items:
            self._build(0, len(self.order))

    def _build(self, start, end):
        idx = self.order[start:end]
        xs = [self.items[i][4] for i in idx]
        ys = [self.items[i][5] for i in idx]
        zs = [self.items[i][6] for i in idx]
        node = [min(xs), max(xs), min(ys), max(ys), min(zs), max(zs),
                max(self.items[i][3] for i in idx), -1, -1, start, end]
        me = len(self.nodes)
        self.nodes.append(node)
        if end - start > LEAF:
            spans = (node[1] - node[0], node[3] - node[2], node[5] - node[4])
            axis = 4 + spans.index(max(spans))
            idx.sort(key=lambda i: self.items[i][axis])
            self.order[start:end] = idx
            mid = (start + end) // 2
            node[7] = self._build(start, mid)
            node[8] = self._build(mid, end)
        return me

    @staticmethod
    def _least(node, x, y, z):
        """The least distance (m) from (x, y, z) on the unit sphere to anything in the box."""
        dx = node[0] - x if x < node[0] else (x - node[1] if x > node[1] else 0.0)
        dy = node[2] - y if y < node[2] else (y - node[3] if y > node[3] else 0.0)
        dz = node[4] - z if z < node[4] else (z - node[5] if z > node[5] else 0.0)
        chord = math.sqrt(dx * dx + dy * dy + dz * dz)
        return 2 * B_LOW * math.asin(min(1.0, chord / 2))

    def best(self, i, score):
        """
        The item maximising `score(h_s, h_q, d)` over the items higher than item i, where score
        grows with h_q and falls with d; None if nothing scores above `floor` (see callers).
        `score(h_s, h_q, d)` must accept d = 0 (a node touching s) and return +inf.
        """
        _, lat, lon, hs, x, y, z = self.items[i]
        cands = []                 # (score upper bound, score lower bound, item index)
        floor = -math.inf          # the best lower bound so far: nothing under it can win
        stack = [0]
        while stack:
            node = self.nodes[stack.pop()]
            if node[6] <= hs or score(hs, node[6], self._least(node, x, y, z)) <= floor:
                continue
            if node[7] < 0:
                for j in self.order[node[9]:node[10]]:
                    item = self.items[j]
                    if j == i or item[3] <= hs:
                        continue
                    chord = math.dist((x, y, z), item[4:7])
                    d = 2 * EARTH * math.asin(min(1.0, chord / 2))
                    up = score(hs, item[3], d * (1 - SLACK))
                    if up <= floor:
                        continue
                    low = score(hs, item[3], d * (1 + SLACK))
                    cands.append((up, low, j))
                    floor = max(floor, low)
            else:
                # the nearer child last, so that it is opened first
                a, b = self.nodes[node[7]], self.nodes[node[8]]
                if self._least(a, x, y, z) < self._least(b, x, y, z):
                    stack += [node[8], node[7]]
                else:
                    stack += [node[7], node[8]]
        # What the estimate could not tell apart is decided by the exact geodesic, as before.
        best, best_score = None, -math.inf
        for up, _, j in cands:
            if up < floor:
                continue
            q = self.items[j]
            d = Geodesic.WGS84.Inverse(lat, lon, q[1], q[2])['s12']
            s = score(hs, q[3], d)
            if s > best_score:
                best, best_score = j, s
        return (self.items[best][0], best_score) if best is not None else (None, None)


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
    return score


def slope_parents(summits, keys=None, progress=None):
    """{key: (parent key or None, slope)} for `keys` (default: all) among `summits`."""
    return _parents(summits, keys, slope, positive=False, progress=progress)


def horizon_parents(summits, keys=None, refraction=0.0, progress=None):
    """{key: (parent key or None, angle)}; a parent only where the angle is positive."""
    return _parents(summits, keys, angle(refraction), positive=True, progress=progress)


def _parents(summits, keys, score, positive, progress=None):
    """`progress(done, total)`, if given, is called as the search goes (every 1,000 summits) and at its end."""
    tree = SummitTree(summits)
    wanted = None if keys is None else set(keys)
    total = len(tree.items) if wanted is None else sum(1 for item in tree.items if item[0] in wanted)
    out = {}
    for i, item in enumerate(tree.items):
        if wanted is not None and item[0] not in wanted:
            continue
        parent, value = tree.best(i, score)
        if positive and value is not None and value <= 0:
            parent, value = None, None
        out[item[0]] = (parent, value)
        if progress and len(out) % 1000 == 0:
            progress(len(out), total)
    if progress:
        progress(len(out), total)
    return out
