"""
Score readings of the prominence parent against the database's: for each summit the lidar
and the database share, the parent each reading gives, and whether it is the database's.

    uv run --group lidar python lidar/score_parents.py CASES.json OUT.json

CASES.json: [{"name", "pk", "lat", "lon", "db_parent": {"name", "pk", "lat", "lon"}}, ...].
"""
import json
import math
import os
import sys

import numpy as np
import rasterio
from pyproj import Transformer

from parents import DivideTree
from prominence import SOURCE, Grid, pool


def main():
    cases, out = json.load(open(sys.argv[1])), sys.argv[2]
    top, at = pool(SOURCE, os.path.expanduser('~/.cache/treeline/lidar'), 10, 8, None)
    h, w = top.shape
    flat = np.ascontiguousarray(top).ravel()
    tree = DivideTree(flat, w, h)
    with rasterio.open(SOURCE) as d:
        grid = Grid(np.zeros((1, 1), np.float32), at, 10, 0, (0, 0), d.transform)
        grid.h, grid.w, grid.W = h, w, w
        grid.filled = np.zeros((h, w), np.bool_)
        to_tm = Transformer.from_crs(4326, d.crs, always_xy=True)
        to_ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
        inv = ~d.transform

        def cell(lat, lon):
            c, r = inv * to_tm.transform(lon, lat)
            return (int(r) // 10) * w + int(c) // 10

        def where(i):
            r, c = grid.pixel(i)
            e, n = d.transform * (c + 0.5, r + 0.5)
            lon, lat = to_ll.transform(e, n)
            return lat, lon

    def dist(a, b):
        return math.hypot((a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])), (a[0] - b[0]) * 110574)

    results, score = [], {'walk': 0}
    for case in cases:
        s = cell(case['lat'], case['lon'])
        if s not in tree.edge_of:
            s = max((s + dr * w + dc for dr in (-1, 0, 1) for dc in (-1, 0, 1)), key=lambda q: flat[q])
        db = (case['db_parent']['lat'], case['db_parent']['lon'])
        row = dict(case)
        for how, p in (('walk', tree.parent(s)),):
            at = where(p) if p >= 0 else None
            ok = at is not None and dist(at, db) < 150
            row[how] = dict(at=at, ok=ok)
            score[how] += ok
        results.append(row)
    json.dump(results, open(out, 'w'), ensure_ascii=False)
    n = len(results)
    for how, k in score.items():
        print(f'{how:8}: {k}/{n} = {100 * k / n:.1f} % agree with the database', flush=True)


if __name__ == '__main__':
    main()
