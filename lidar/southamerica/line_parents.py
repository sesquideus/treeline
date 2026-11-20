"""
The line parent (`tiled.walk_line_parents`) of every summit of at least the cutoff, from the
South America flood `tiled.py` stored: the first higher summit across its key col, among
those of at least the cutoff.

    cd lidar && uv run --project .. --group lidar python southamerica/line_parents.py WORK \
        --cutoff 100 --out LINE.json

Output: [{lat, lon, h, line: [lat, lon] or null}], positions of the tops in the flood;
`line_changeset.py` finds the database's summits standing there.
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tiled import Grid, load_deaths, walk_line_parents  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('work')
    ap.add_argument('--cutoff', type=float, default=100.0)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    g = Grid(a.work)
    z = g.array()
    t0 = time.time()
    tops, col, isle, hz, cz, farc = load_deaths(a.work)
    prom = np.where(col >= 0, hz - cz, hz)
    sel = np.flatnonzero(prom >= a.cutoff)
    print(f'{len(tops)} deaths loaded in {time.time() - t0:.0f}s; {len(sel)} of P >= {a.cutoff:g}', flush=True)
    t0 = time.time()
    line = walk_line_parents(z, g.rows, g.cols, tops, col, isle, hz, cz, farc, tops[sel], a.cutoff)
    print(f'walked in {time.time() - t0:.0f}s', flush=True)
    at = lambda cell: list(g.latlon(*divmod(int(cell), g.cols)))
    out = [dict(zip(('lat', 'lon'), at(tops[i])), h=round(float(hz[i]), 2),
                line=at(p) if p >= 0 else None)
           for i, p in zip(sel.tolist(), line.tolist())]
    json.dump(out, open(a.out, 'w'), indent=0)
    print(f'{len(out)} summits, {sum(1 for r in out if r["line"] is None)} without a line parent -> {a.out}')


if __name__ == '__main__':
    main()
