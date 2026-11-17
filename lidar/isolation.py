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
        if best_at is None:
            out.append(dict(pk=s['pk'], name=s['point']['name'], nhp=None, note='nothing higher in the data'))
            continue
        nhp_ll = lonlat(*best_at)
        # is the data's edge nearer? then the real one may be beyond it
        edge = next((dist for dist, _, _ in nearest(levels, TR, TC, lambda lvl, z, hole: hole)), math.inf)
        beyond = edge * CELL < best

        # The nearest summit higher than this one, from the NHP in a straight line — any summit
        # on the ground (P >= 30 in the lidar), catalogued or not; named after the database's
        # summit standing on it, if one does. From the database's own NHPs this reading gives
        # 98 % of its isolation parents. (Walking uphill from the NHP, or through the higher
        # ground, does worse: from Javorie's NHP both lead past Vrchdetva, the nearest.)
        k_lat = math.cos(math.radians(nhp_ll[0]))
        higher = np.flatnonzero(cand['h'] > h)
        nhn = None
        if len(higher):
            dd = (cand['lat'][higher] - nhp_ll[0]) ** 2 + ((cand['lon'][higher] - nhp_ll[1]) * k_lat) ** 2
            j = int(higher[np.argmin(dd)])
            at_ = (float(cand['lat'][j]), float(cand['lon'][j]))
            named = min(summits, key=lambda x: (x['point']['lat'] - at_[0]) ** 2
                        + ((x['point']['lon'] - at_[1]) * k_lat) ** 2)
            close = math.hypot((named['point']['lat'] - at_[0]) * 110574,
                               (named['point']['lon'] - at_[1]) * 111320 * k_lat) < 60
            nhn = dict(pk=named['pk'] if close else None, name=named['point']['name'] if close else None,
                       at=(round(at_[0], 6), round(at_[1], 6)),
                       prominence=round(float(cand['prominence'][j]), 1))
        db_nhp = s.get('nhp')
        nhp = nhp_ll
        out.append(dict(pk=s['pk'], name=s['point']['name'], top=lonlat(tr, tc), nhp=nhp,
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
