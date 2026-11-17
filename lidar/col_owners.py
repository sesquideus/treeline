"""
Whose key col is this saddle? For each (lat, lon) given — a col that stopped being some
summit's key col — the summit the whole-country flood has die there: the highest peak on its
lower side, meeting higher ground at it. With its prominence, its key col at 1 m and
TreeLine's prominence parent (the divide-tree walk of `parents.py`). On the lidar alone.

    uv run --group lidar python lidar/col_owners.py QUERIES.json OUT.json

QUERIES.json: [{"key": ..., "lat": ..., "lon": ...}, ...]. A query matches the saddle
within --radius metres of it whose summit is the most prominent.
"""
import argparse
import json
import os
import time

import numpy as np
import rasterio
from numba import njit
from pyproj import Transformer

from parents import DivideTree
from prominence import SOURCE, Grid, find, pool, refine_col


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('queries')
    ap.add_argument('out')
    ap.add_argument('--radius', type=float, default=60.0)
    ap.add_argument('--cell', type=int, default=10)
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    args = ap.parse_args()
    queries = json.load(open(args.queries))

    top, at = pool(SOURCE, args.work, args.cell, 8, None)
    h, w = top.shape
    flat = np.ascontiguousarray(top).ravel()
    t = time.time()
    tree = DivideTree(flat, w, h)
    prom, d_col, d_peak, d_sd, d_sb = tree.prom, tree.col, tree.peak, tree.sd, tree.sb
    print(f'flooded in {time.time() - t:.0f}s, {len(d_col)} deaths', flush=True)

    with rasterio.open(SOURCE) as d:
        grid = Grid(np.zeros((1, 1), np.float32), at, args.cell, 0, (0, 0), d.transform)
        grid.h, grid.w, grid.W = h, w, w
        grid.filled = np.zeros((h, w), np.bool_)
        to_tm = Transformer.from_crs(4326, d.crs, always_xy=True)
        to_ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
        bpv = Transformer.from_crs('EPSG:4937', 'EPSG:4258+8357', always_xy=True, allow_ballpark=False)
        inv = ~d.transform

        def place(rc, height):
            e, n = d.transform * (rc[1] + 0.5, rc[0] + 0.5)
            lon, lat = to_ll.transform(e, n)
            return dict(lat=round(lat, 6), lon=round(lon, 6), h=round(float(height), 2),
                        bpv=round(float(bpv.transform(lon, lat, float(height))[2]), 2))

        rad = int(np.ceil(args.radius / args.cell))
        out = []
        for q in queries:
            c, r = inv * to_tm.transform(q['lon'], q['lat'])
            r, c = int(r) // args.cell, int(c) // args.cell
            cells = [(r + dr) * w + (c + dc) for dr in range(-rad, rad + 1) for dc in range(-rad, rad + 1)
                     if 0 <= r + dr < h and 0 <= c + dc < w]
            hits = np.flatnonzero(np.isin(d_col, cells))
            if not len(hits):
                out.append(dict(q, found=None))
                print(f'{q["key"]}: no saddle within {args.radius:g} m')
                continue
            # The saddle there that matters: the one whose summit is the most prominent. The
            # highest one within reach can be a hump's on a ridge the col point is a little off.
            i = max(hits, key=lambda k: prom[d_peak[k]])
            s = int(d_peak[i])
            refined = refine_col(d, grid, int(d_col[i]), int(d_sd[i]), int(d_sb[i]), 150)
            col_h, col_rc = (refined[0], refined[1]) if refined else (float(flat[d_col[i]]), grid.pixel(d_col[i]))
            p = tree.parent(s)
            summit = place(grid.pixel(s), flat[s])
            out.append(dict(q, summit=summit, col=place(col_rc, col_h),
                            prominence=round(summit['h'] - col_h, 2),
                            parent=place(grid.pixel(p), flat[p]) if p >= 0 else None))
            print(f'{q["key"]}: summit {summit["lat"]}, {summit["lon"]} {summit["bpv"]:.1f} m, '
                  f'P {summit["h"] - col_h:.1f}, col {col_h:.1f} (ellipsoidal)', flush=True)
    json.dump(out, open(args.out, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
