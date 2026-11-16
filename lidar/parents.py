"""
TreeLine's prominence parent for the summits `prominence.py` found: the nearest peak with
greater prominence, walking up from the key col — not the highest peak beyond it, which is
what the flood hands out. On the lidar alone, from the pooled grid `prominence.py` cached.

    uv run --group lidar python lidar/parents.py [--min-prominence 100] [--out DIR]

The walk runs along the divide tree. When a summit dies, its key col's far side is climbed
by steepest ascent to the first summit up the ridge — `up`, the next peak along the divide
tree. From there each summit's own key col leads on (its `up`), towards ever higher ground,
until a summit more prominent than the one the walk started from. Summits too small to
record are walked past just the same: they are never more prominent.

(The summit owning the far side when the flood gets there will not do: by the col's level
the nearby peaks have usually merged into the island's highest one already.)
"""
import argparse
import csv
import os
import time

import numpy as np
import rasterio
from numba import njit
from pyproj import Transformer

from prominence import SOURCE, Grid, find, pool


@njit(cache=True)
def ascend(z, w, h, p):
    """Steepest ascent from `p` to a local maximum (a plateau's first cell will do)."""
    while True:
        y = p // w
        x = p - y * w
        best, top = -1, z[p]
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                yy = y + dy
                xx = x + dx
                if (dy == 0 and dx == 0) or yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if z[q] > top:
                    best, top = q, z[q]
        if best < 0:
            return p
        p = best


@njit(cache=True)
def flood_owners(z, order, w, h):
    n = w * h
    parent = np.full(n, -1, np.int32)
    peak = np.full(n, -1, np.int32)
    owner = np.full(n, -1, np.int32)          # per cell: its component's summit when it joined
    up = np.full(n, -1, np.int32)             # per summit: the territory its key col leads into
    prom = np.full(n, np.inf, np.float32)     # per summit; inf for what never dies
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
            d = peak[r]
            prom[d] = z[d] - z[p]
            up[d] = owner[ascend(z, w, h, via[best])]
            parent[r] = b
        parent[p] = b
        owner[p] = peak[b]
    return up, prom


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', default=SOURCE)
    ap.add_argument('--min-prominence', type=float, default=100.0)
    ap.add_argument('--margin', type=float, default=20.0)
    ap.add_argument('--cell', type=int, default=10)
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    ap.add_argument('--out', default='.')
    args = ap.parse_args()

    top, at = pool(args.source, args.work, args.cell, 8, None)
    h, w = top.shape
    flat = np.ascontiguousarray(top).ravel()
    t = time.time()
    order = np.argsort(-flat, kind='stable').astype(np.int32)
    up, prom = flood_owners(flat, order, w, h)
    del order
    print(f'flooded in {time.time() - t:.0f}s', flush=True)

    with rasterio.open(args.source) as d:
        grid = Grid(np.zeros((1, 1), np.float32), at, args.cell, 0, (0, 0), d.transform)
        grid.h, grid.w, grid.W = h, w, w
        grid.filled = np.zeros((h, w), np.bool_)
        to_ll = Transformer.from_crs(d.crs, 4326, always_xy=True)

        def where(index):
            r, c = grid.pixel(index)
            e, n = d.transform * (c + 0.5, r + 0.5)
            lon, lat = to_ll.transform(e, n)
            return round(lat, 6), round(lon, 6)

    keep = args.min_prominence - args.margin
    summits = np.flatnonzero((prom >= keep) & np.isfinite(prom))
    path = os.path.join(args.out, f'p{args.min_prominence:g}-parents.csv')
    with open(path, 'w', newline='') as f:
        out = csv.writer(f)
        out.writerow(['lat', 'lon', 'pooled_prominence', 'parent_lat', 'parent_lon',
                      'parent_pooled_prominence', 'line_parent_lat', 'line_parent_lon'])
        for s in summits:
            line = int(up[s])
            p = line
            # Each step's key col is no lower than the last one's, so the walk climbs; the
            # cap is only against a plateau's ties.
            for _ in range(100000):
                if p < 0 or prom[p] > prom[s]:
                    break
                p = int(up[p])
            out.writerow([*where(s), round(float(prom[s]), 2),
                          *(where(p) if p >= 0 else (None, None)),
                          round(float(prom[p]), 2) if p >= 0 and np.isfinite(prom[p]) else 'top',
                          *(where(line) if line >= 0 else (None, None))])
    print(f'{len(summits)} summits -> {path}')


if __name__ == '__main__':
    main()
