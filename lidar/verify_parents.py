"""
Is the lidar walk's prominence parent certain on the 1 m data? The walk runs on the pooled
10 m grid, where a key col can come out a few metres high, so every comparison that decided
it is measured again at 1 m: the summit's own prominence, the parent's, and that of every
summit the walk passed on the way that came within --near metres of the summit's prominence.
Certain when the parent beats the summit by more than --margin and every one passed falls
short of it by more than --margin.

    uv run --group lidar python lidar/verify_parents.py CASES.json OUT.json

CASES.json: [{"name", "lat", "lon", "db_parent": {"name", "lat", "lon"}}, ...], the summit as
the lidar has it (its 1 m top).
"""
import argparse
import json
import math
import os

import numpy as np
import rasterio
from pyproj import Transformer

from parents import DivideTree
from prominence import SOURCE, Grid, pool, refine_col


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cases')
    ap.add_argument('out')
    ap.add_argument('--near', type=float, default=30.0)
    ap.add_argument('--margin', type=float, default=3.0)
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    args = ap.parse_args()
    cases = json.load(open(args.cases))

    top, at = pool(SOURCE, args.work, 10, 8, None)
    h, w = top.shape
    flat = np.ascontiguousarray(top).ravel()
    tree = DivideTree(flat, w, h)
    death = {int(p): i for i, p in enumerate(tree.peak)}
    out = []
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
            return round(lat, 6), round(lon, 6)

        def dist(a, b):
            return math.hypot((a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])), (a[0] - b[0]) * 110574)

        fine = {}

        def prom1m(p):
            """The summit's prominence with its key col measured at 1 m."""
            if p not in fine:
                i = death.get(p)
                if i is None:
                    fine[p] = math.inf
                else:
                    r = refine_col(d, grid, int(tree.col[i]), int(tree.sd[i]), int(tree.sb[i]), 150)
                    fine[p] = float(flat[p] - (r[0] if r else flat[tree.col[i]]))
            return fine[p]

        for case in cases:
            s = cell(case['lat'], case['lon'])
            if s not in death:      # the summit's top is in a neighbouring cell
                s = max((s + dr * w + dc for dr in (-1, 0, 1) for dc in (-1, 0, 1)), key=lambda q: flat[q])
            ps = float(tree.prom[s])
            # the walk again, keeping every summit it passes
            path, p = [], tree.far.get(s, -1)
            while p >= 0 and not tree.prom[p] > ps:
                path.append(p)
                p = tree.far.get(p, -1)
            parent = p
            s1 = prom1m(s)
            passed = [(q, prom1m(q)) for q in path if tree.prom[q] > ps - args.near]
            p1 = prom1m(parent) if parent >= 0 else None
            db = (case['db_parent']['lat'], case['db_parent']['lon'])
            on_path = next((k for k, q in enumerate(path + [parent]) if dist(where(q), db) < 150), None)
            sure = (p1 is not None and p1 - s1 > args.margin
                    and all(s1 - v > args.margin for _, v in passed))
            out.append(dict(name=case['name'], prominence=round(s1, 2),
                            parent=dict(at=where(parent), prominence=round(p1, 2) if p1 is not None else None),
                            passed=[dict(at=where(q), prominence=round(v, 2)) for q, v in passed],
                            db_parent_on_path=on_path, steps=len(path), sure=sure))
            print(f'{case["name"]:28} P {s1:7.1f} -> parent P {p1 if p1 is None else round(p1, 1)}; '
                  f'{len(passed)} close ones passed {[round(v, 1) for _, v in passed]}; '
                  f'DB parent {"on the path at step " + str(on_path) if on_path is not None else "not on the path"}; '
                  f'{"SURE" if sure else "not sure"}', flush=True)
    json.dump(out, open(args.out, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
