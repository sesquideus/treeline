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

from parents import ascend
from prominence import SOURCE, Grid, find, pool, refine_col


@njit(cache=True)
def flood_deaths(z, order, w, h):
    """Every death: col, peak, a cell beside the col on either side; and `up`/`prom` per peak."""
    n = w * h
    parent = np.full(n, -1, np.int32)
    peak = np.full(n, -1, np.int32)
    owner = np.full(n, -1, np.int32)
    up = np.full(n, -1, np.int32)
    prom = np.full(n, np.inf, np.float32)
    cap = n // 8
    d_col = np.empty(cap, np.int32)
    d_peak = np.empty(cap, np.int32)
    d_sd = np.empty(cap, np.int32)
    d_sb = np.empty(cap, np.int32)
    c = 0
    roots = np.empty(8, np.int64)
    via = np.empty(8, np.int64)
    for k in range(n):
        p = order[k]
        if z[p] == -np.inf:
            break
        y = p // w
        x = p - y * w
        m = 0
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if parent[q] < 0:
                    continue
                r = find(parent, q)
                dup = False
                for j in range(m):
                    if roots[j] == r:
                        dup = True
                if not dup:
                    roots[m] = r
                    via[m] = q
                    m += 1
        if m == 0:
            parent[p] = p
            peak[p] = p
            owner[p] = p
            continue
        best = 0
        for j in range(1, m):
            if z[peak[roots[j]]] > z[peak[roots[best]]]:
                best = j
        b = roots[best]
        for j in range(m):
            r = roots[j]
            if r == b:
                continue
            dpk = peak[r]
            prom[dpk] = z[dpk] - z[p]
            up[dpk] = owner[ascend(z, w, h, via[best])]
            if c < cap:
                d_col[c] = p
                d_peak[c] = dpk
                d_sd[c] = via[j]
                d_sb[c] = via[best]
                c += 1
            parent[r] = b
        parent[p] = b
        owner[p] = peak[b]
    return up, prom, d_col[:c], d_peak[:c], d_sd[:c], d_sb[:c]


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
    order = np.argsort(-flat, kind='stable').astype(np.int32)
    up, prom, d_col, d_peak, d_sd, d_sb = flood_deaths(flat, order, w, h)
    del order
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
            p = int(up[s])
            for _ in range(100000):                       # the walk, as in parents.py
                if p < 0 or prom[p] > prom[s]:
                    break
                p = int(up[p])
            summit = place(grid.pixel(s), flat[s])
            out.append(dict(q, summit=summit, col=place(col_rc, col_h),
                            prominence=round(summit['h'] - col_h, 2),
                            parent=place(grid.pixel(p), flat[p]) if p >= 0 else None))
            print(f'{q["key"]}: summit {summit["lat"]}, {summit["lon"]} {summit["bpv"]:.1f} m, '
                  f'P {summit["h"] - col_h:.1f}, col {col_h:.1f} (ellipsoidal)', flush=True)
    json.dump(out, open(args.out, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
