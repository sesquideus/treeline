"""
Isolation from the lidar: for each summit, the nearest ground higher than its top (the
nearest higher point, NHP) and the summit that ground belongs to (the nearest higher
neighbour, NHN, TreeLine's isolation parent); against the database's.

    uv run --group lidar python lidar/isolation.py DB.json OUT.json

The NHP is the nearest 1 m pixel higher than the summit's own top. Scanning for it pixel by
pixel would read billions of them per summit; instead a pyramid of maxima over the pooled
10 m grid (10 m, 100 m, 1 km, 10 km) is searched best-first by distance, skipping every block
whose highest point is not higher, and only the 10 m cells that could hold the answer are
read at 1 m. Where the edge of the data is nearer than what was found, the real NHP may be
across it, and that is said instead.

The NHN: the summit higher than the summit nearest to the NHP, in a straight line — any summit
on the ground, from lidar/summits.py (P >= 30), named after the catalogued one on it if any.
"""
import argparse
import heapq
import itertools
import json
import math
import os

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from prominence import SOURCE, pool

CELL = 10


def pyramid(top):
    """Maxima, and whether any cell lacks data, at 10 m, 100 m, 1 km and 10 km."""
    levels = [(np.asarray(top), ~np.isfinite(np.asarray(top)))]
    for _ in range(3):
        z, hole = levels[-1]
        h, w = z.shape
        H, W = -(-h // 10), -(-w // 10)
        zp = np.full((H * 10, W * 10), -np.inf, np.float32)
        zp[:h, :w] = z
        hp = np.ones((H * 10, W * 10), bool)
        hp[:h, :w] = hole
        levels.append((zp.reshape(H, 10, W, 10).max(axis=(1, 3)), hp.reshape(H, 10, W, 10).any(axis=(1, 3))))
    return levels


def nearest(levels, r, c, test):
    """
    Best-first over the pyramid from 10 m cell (r, c): the 10 m cells nearest to it for which
    `test(level, z, hole)` holds, as (distance in 10 m cells, row, col); stops once nothing
    nearer can be left. Yields candidates in order of their lower-bound distance.
    """
    heap = []
    top = len(levels) - 1
    z, hole = levels[top]
    for i in range(z.shape[0]):
        for j in range(z.shape[1]):
            if test(top, z[i, j], hole[i, j]):
                heapq.heappush(heap, (box_dist(r, c, i, j, 10 ** top), top, i, j))
    while heap:
        dist, lvl, i, j = heapq.heappop(heap)
        if lvl == 0:
            yield dist, i, j
            continue
        z, hole = levels[lvl - 1]
        for a in range(i * 10, min(i * 10 + 10, z.shape[0])):
            for b in range(j * 10, min(j * 10 + 10, z.shape[1])):
                if test(lvl - 1, z[a, b], hole[a, b]):
                    heapq.heappush(heap, (box_dist(r, c, a, b, 10 ** (lvl - 1)), lvl - 1, a, b))


def box_dist(r, c, i, j, size):
    """Distance in 10 m cells from cell (r, c) to the block (i, j) of `size` cells a side."""
    dr = max(i * size - r, 0, r - (i * size + size - 1))
    dc = max(j * size - c, 0, c - (j * size + size - 1))
    return math.hypot(dr, dc)


def nearest_higher(d, levels, tr, tc, h):
    """
    The nearest 1 m pixel higher than `h` from source pixel (tr, tc): (metres, (row, col)), and
    whether the data's edge is nearer (then the real one may lie beyond it). (inf, None) if
    nothing in the data is higher.
    """
    TR, TC = tr // CELL, tc // CELL
    best, best_at = math.inf, None
    for dist, a, b in nearest(levels, TR, TC, lambda lvl, z, hole: z > h):
        if (dist - 1.5) * CELL > best:
            break
        zz = d.read(1, window=Window(b * CELL, a * CELL, CELL, CELL)).astype(np.float64)
        ys, xs = np.nonzero((zz > h) & (zz < 1e30))
        if len(ys):
            dd = np.hypot(a * CELL + ys - tr, b * CELL + xs - tc)
            j = int(np.argmin(dd))
            if dd[j] < best:
                best, best_at = float(dd[j]), (a * CELL + int(ys[j]), b * CELL + int(xs[j]))
    edge = next((dist for dist, _, _ in nearest(levels, TR, TC, lambda lvl, z, hole: hole)), math.inf)
    return best, best_at, edge * CELL < best


# ------------------------------------------------------------------------- any terrain (terrain.py)
#
# The same search over any reader of `terrain.py`: best-first down its boxes by their lower-bound
# distance, skipping every box whose highest point is not higher; only the leaves reached are read.
# The terrain is a continuous surface, bilinear between cell centres: the nearest higher point is
# the nearest place where that surface rises above the summit, not the nearest higher cell centre
# (on 1" cells, 31 x 21 m in Ukraine, the two differ by 10-20 m).

EPS = 0.01          # m: "higher" is higher than the summit's top by this much


def search(terrain, lon, lat, t, limit=math.inf):
    """
    The nearest point of `terrain` from (lon, lat) where the surface reaches height `t`: a dict of
    metres, lon, lat, the nearest higher cell centre's metres (`centre_m`), `anchor` (lon, lat of
    the highest corner of the cell square the point lies on, where a climb starts), `source`, and
    `burned` (the burned cells among that square's corners above t: (height, model, label)); None
    if nothing within `limit` m reaches t.
    """
    heap, n = [], itertools.count()
    for box, zmax, node in terrain.roots():
        if zmax > t:
            heapq.heappush(heap, (terrain_box_bound(lon, lat, box), next(n), node))
    best, found, slack = math.inf, [], terrain.diag
    while heap:
        b, _, node = heapq.heappop(heap)
        if b > min(best + slack, limit):
            break
        if terrain.is_leaf(node):
            p = terrain.patch(node)
            with np.errstate(invalid='ignore'):
                rr, cc = np.nonzero(p.z[1:-1, 1:-1] >= t)
            if not len(rr):
                continue
            rr, cc = rr + 1, cc + 1
            d = _dist(lon, lat, *p.lonlat(rr, cc))
            best = min(best, float(d.min()))
            found.append((p, rr, cc, d))
            continue
        for box, zmax, child in terrain.children(node):
            if zmax > t:
                heapq.heappush(heap, (terrain_box_bound(lon, lat, box), next(n), child))
    if not found or best > limit:
        return None
    return refine(found, lon, lat, t, best, slack)


def first_leaf(terrain, lon, lat, t, limit=math.inf):
    """ The lower-bound metres to the nearest leaf of `terrain` whose maximum reaches t (inf if none
    within `limit`): where higher ground may begin, without reading it. """
    heap, n = [], itertools.count()
    for box, zmax, node in terrain.roots():
        if zmax > t:
            heapq.heappush(heap, (terrain_box_bound(lon, lat, box), next(n), node))
    while heap:
        b, _, node = heapq.heappop(heap)
        if b > limit:
            break
        if terrain.is_leaf(node):
            return b
        for box, zmax, child in terrain.children(node):
            if zmax > t:
                heapq.heappush(heap, (terrain_box_bound(lon, lat, box), next(n), child))
    return math.inf


def terrain_box_bound(lon, lat, box):
    from terrain import box_bound as bb
    return bb(lon, lat, box)


def _dist(lon, lat, plon, plat):
    from terrain import distances
    return distances(lon, lat, plon, plat)


def contour_samples(z00, z01, z10, z11, t, n=64):
    """
    Points (v, u) of unit cell squares (v down the rows, u along the columns) on the boundary of
    where the bilinear surface through the corners reaches t: the corners and n + 1 points along
    each edge where it does, the exact crossings of t on the edges, and the contour f = t inside,
    sampled along u and along v (so steep runs of it are as dense as flat ones). Arrays of the
    corners (S,) give v, u and `ok` of shape (S, K). The nearest point of the region to anything
    outside it lies on this boundary; sampling misses it by a second-order amount (well under 0.1 m
    on a 30 m cell), and not at all where it is a corner or an edge crossing.
    """
    z00, z01, z10, z11 = (np.asarray(a, np.float64)[:, None] for a in (z00, z01, z10, z11))
    a, b, c, d = z00, z01 - z00, z10 - z00, z11 - z10 - z01 + z00
    g = np.linspace(0.0, 1.0, n + 1)[None, :]
    S = z00.shape[0]
    one, zero = np.ones((S, n + 1)), np.zeros((S, n + 1))
    vs, us = [], []
    vs += [np.zeros((S, 1)), np.zeros((S, 1)), np.ones((S, 1)), np.ones((S, 1))]
    us += [np.zeros((S, 1)), np.ones((S, 1)), np.zeros((S, 1)), np.ones((S, 1))]
    G = np.broadcast_to(g, (S, n + 1))
    vs += [zero, one, G, G]
    us += [G, G, zero, one]
    with np.errstate(divide='ignore', invalid='ignore'):
        vs += [np.zeros((S, 1)), np.ones((S, 1)), (t - z00) / (z10 - z00), (t - z01) / (z11 - z01)]
        us += [(t - z00) / (z01 - z00), (t - z10) / (z11 - z10), np.zeros((S, 1)), np.ones((S, 1))]
        vs.append((t - a - b * g) / (c + d * g))
        us.append(np.broadcast_to(g, (S, n + 1)))
        us.append((t - a - c * g) / (b + d * g))
        vs.append(np.broadcast_to(g, (S, n + 1)))
    v, u = np.concatenate(vs, axis=1), np.concatenate(us, axis=1)
    with np.errstate(invalid='ignore'):
        f = a + b * u + c * v + d * u * v
        ok = np.isfinite(v) & np.isfinite(u) & (v >= -1e-12) & (v <= 1 + 1e-12) & (u >= -1e-12) & (u <= 1 + 1e-12) \
            & (f >= t - 1e-6)
    return np.clip(np.nan_to_num(v), 0, 1), np.clip(np.nan_to_num(u), 0, 1), ok


def refine(found, lon, lat, t, best, slack):
    """
    From the higher cells the search found ([(patch, rows, cols, metres)]), the nearest point where
    the bilinear surface reaches t: on every cell square with a higher corner within `best +
    slack` (no square farther can hold a nearer point), see `contour_samples`.
    """
    win = None
    for p, rr, cc, d in found:
        sel = d < best + slack
        if not sel.any():
            continue
        H, W = p.z.shape
        sq = set()
        for r, c in zip(rr[sel], cc[sel]):
            for i in (r - 1, r):
                for j in (c - 1, c):
                    if 0 <= i < H - 1 and 0 <= j < W - 1:
                        sq.add((int(i), int(j)))
        # the cell centres themselves: points of the region even where a square has no data
        cand = [(rr[sel].astype(np.float64), cc[sel].astype(np.float64), np.stack([rr[sel], cc[sel]], 1))]
        if sq:
            sq = np.array(sorted(sq))
            i, j = sq[:, 0], sq[:, 1]
            z00, z01, z10, z11 = p.z[i, j], p.z[i, j + 1], p.z[i + 1, j], p.z[i + 1, j + 1]
            full = np.isfinite(z00) & np.isfinite(z01) & np.isfinite(z10) & np.isfinite(z11)
            if full.any():
                i, j = i[full], j[full]
                v, u, ok = contour_samples(z00[full], z01[full], z10[full], z11[full], t)
                R = (i[:, None] + v)[ok]
                C = (j[:, None] + u)[ok]
                corner = np.broadcast_to(np.arange(len(i))[:, None], ok.shape)[ok]
                cand.append((R, C, np.stack([i[corner], j[corner]], 1)))
        for R, C, ij in cand:
            if not len(R):
                continue
            plon, plat = p.lonlat(R, C)
            dd = _dist(lon, lat, plon, plat)
            k = int(np.argmin(dd))
            if win is None or dd[k] < win[0]:
                win = (float(dd[k]), float(plon[k]), float(plat[k]), p, tuple(ij[k]), (R[k], C[k]))
    metres, plon, plat, p, (i, j), (R, C) = win
    # the square's corners (or the cell itself, for a centre)
    if R == i and C == j:
        corners = [(i, j)]
    else:
        corners = [(a, b) for a in (i, i + 1) for b in (j, j + 1) if 0 <= a < p.z.shape[0] and 0 <= b < p.z.shape[1]]
    hi = max(corners, key=lambda q: p.z[q] if np.isfinite(p.z[q]) else -np.inf)
    alon, alat = p.lonlat(np.array([hi[0]], np.float64), np.array([hi[1]], np.float64))
    burned = [p.burned[q] for q in corners if q in p.burned and p.z[q] >= t]
    return dict(metres=metres, lon=plon, lat=plat, centre_m=best, anchor=(float(alon[0]), float(alat[0])),
                source=p.source, burned=burned)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db')
    ap.add_argument('out')
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    ap.add_argument('--candidates', type=float, default=30,
                    help='summits of at least this prominence (lidar/summits.py) can be the NHN')
    args = ap.parse_args()
    summits = json.load(open(args.db))['summits']
    by_pk = {s['pk']: s for s in summits}

    top, at = pool(SOURCE, args.work, CELL, 8, None)
    top = np.asarray(top)
    H, W = top.shape
    levels = pyramid(top)
    flat = top.ravel()
    d = rasterio.open(SOURCE)
    tm = Transformer.from_crs(4326, d.crs, always_xy=True)
    ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
    inv = ~d.transform
    to_ell = Transformer.from_crs('EPSG:4258+8357', 'EPSG:4937', always_xy=True)

    def pix(lat, lon):
        c, r = inv * tm.transform(lon, lat)
        return int(r), int(c)

    def lonlat(r, c):
        e, n = d.transform * (c + 0.5, r + 0.5)
        lon, lat = ll.transform(e, n)
        return round(lat, 6), round(lon, 6)

    # The candidates: every lidar summit of P >= --candidates, and every summit the database
    # has whatever its prominence (Vrchdetva has under 30 m, and is Javorie's).
    lid = np.load(os.path.join(args.work, f'summits-p{args.candidates:g}.npz'))
    db_h = np.array([to_ell.transform(x['point']['lon'], x['point']['lat'], x['point']['alt'])[2] for x in summits])
    cand_pk = np.concatenate([np.full(len(lid['lat']), -1), [x['pk'] for x in summits]])
    cand = dict(lat=np.concatenate([lid['lat'], [x['point']['lat'] for x in summits]]),
                lon=np.concatenate([lid['lon'], [x['point']['lon'] for x in summits]]),
                h=np.concatenate([lid['h'], db_h]),
                prominence=np.concatenate([lid['prominence'],
                                           [x['prominence'] if x['prominence'] is not None else np.nan for x in summits]]))
    out = []
    for k, s in enumerate(summits):
        r, c = pix(s['point']['lat'], s['point']['lon'])
        R, C = r // CELL, c // CELL
        if not (0 <= R < H and 0 <= C < W) or not np.isfinite(top[R, C]):
            continue
        # the summit's own top, at 1 m, within 15 m of the database's point
        z0 = d.read(1, window=Window(c - 15, r - 15, 31, 31)).astype(np.float64)
        if (z0 > 1e30).any():
            continue
        i = np.unravel_index(np.argmax(z0), z0.shape)
        tr, tc, h = r - 15 + i[0], c - 15 + i[1], float(z0[i])
        TR, TC = tr // CELL, tc // CELL

        # the nearest higher 1 m pixel: candidate 10 m cells nearest first, read at 1 m
        best, best_at, beyond = nearest_higher(d, levels, tr, tc, h)
        if best_at is None:
            out.append(dict(pk=s['pk'], name=s['point']['name'], nhp=None, note='nothing higher in the data'))
            continue
        nhp_ll = lonlat(*best_at)

        # The nearest summit higher than this one, from the NHP in a straight line — any summit
        # on the ground (P >= 30 in the lidar), catalogued or not; named after the database's
        # summit standing on it, if one does. From the database's own NHPs this reading gives
        # 98 % of its isolation parents. (Walking uphill from the NHP, or through the higher
        # ground, does worse: from Javorie's NHP both lead past Vrchdetva, the nearest.)
        k_lat = math.cos(math.radians(nhp_ll[0]))
        # Not the summit itself: its own entry in the database can stand higher than its lidar
        # top (a crag the ground model shaved), and would then be its own nearest higher one.
        top_ll = lonlat(tr, tc)
        own = np.hypot((cand['lat'] - top_ll[0]) * 110574,
                       (cand['lon'] - top_ll[1]) * 111320 * math.cos(math.radians(top_ll[0]))) < 20
        own |= cand_pk == s['pk']
        higher = np.flatnonzero((cand['h'] > h) & ~own)
        nhn = None
        if len(higher):
            dd = (cand['lat'][higher] - nhp_ll[0]) ** 2 + ((cand['lon'][higher] - nhp_ll[1]) * k_lat) ** 2
            j = int(higher[np.argmin(dd)])
            at_ = (float(cand['lat'][j]), float(cand['lon'][j]))
            named = min((x for x in summits if x['pk'] != s['pk']),
                        key=lambda x: (x['point']['lat'] - at_[0]) ** 2 + ((x['point']['lon'] - at_[1]) * k_lat) ** 2)
            close = math.hypot((named['point']['lat'] - at_[0]) * 110574,
                               (named['point']['lon'] - at_[1]) * 111320 * k_lat) < 60
            nhn = dict(pk=named['pk'] if close else None, name=named['point']['name'] if close else None,
                       at=(round(at_[0], 6), round(at_[1], 6)),
                       prominence=round(float(cand['prominence'][j]), 1))
        db_nhp = s.get('nhp')
        nhp = nhp_ll
        out.append(dict(pk=s['pk'], name=s['point']['name'], top=lonlat(tr, tc), top_px=(int(tr), int(tc)),
                        top_h=h, nhp_px=(int(best_at[0]), int(best_at[1])), nhp=nhp,
                        isolation_m=round(best, 1), beyond_data=beyond,
                        db_nhp=db_nhp, db_nhp_off_m=round(math.hypot(
                            (db_nhp[1] - nhp[1]) * 111320 * math.cos(math.radians(nhp[0])),
                            (db_nhp[0] - nhp[0]) * 110574), 1) if db_nhp else None,
                        db_isolation_m=round(math.hypot(
                            (db_nhp[1] - s['point']['lon']) * 111320 * math.cos(math.radians(s['point']['lat'])),
                            (db_nhp[0] - s['point']['lat']) * 110574), 1) if db_nhp else None,
                        nhn=nhn,
                        db_nhn=dict(pk=s['isolation_parent_pk'],
                                    name=by_pk[s['isolation_parent_pk']]['point']['name']
                                    if s['isolation_parent_pk'] in by_pk else None) if s['isolation_parent_pk'] else None))
        if k % 200 == 0:
            print(f'{k}/{len(summits)}', flush=True)
    json.dump(out, open(args.out, 'w'), ensure_ascii=False, indent=1)
    print(f'{len(out)} summits', flush=True)


if __name__ == '__main__':
    main()
