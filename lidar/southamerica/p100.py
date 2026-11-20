"""
The P100s around a place, from the South America flood `tiled.py` stored (Copernicus GLO-30 at 1″,
list heights burned in), with every parent their chains need that the database lacks, however far.

    cd lidar && uv run --project .. --group lidar python southamerica/p100.py WORK \
        --center -22.4566,-68.9237 --radius 300 --known KNOWN.json --out P100.json

KNOWN.json: the database's summits there ([{pk, name, lat, lon, alt}]). A top within `--same`
metres (200) of one is that summit. Output: as `tiled.py walk`, each top with its key col, the
summit of the higher ground at the col, whether the col is on the edge of the data, and its parent;
`why` says whether it is around the place or a parent a chain needed.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tiled import Grid, load_deaths, walk_parents  # noqa: E402


def km(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 110.574, (a[1] - b[1]) * 111.32 * k)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('work')
    ap.add_argument('--center', required=True)
    ap.add_argument('--radius', type=float, default=300.0, help='km')
    ap.add_argument('--min-prominence', type=float, default=100.0)
    ap.add_argument('--known', required=True)
    ap.add_argument('--same', type=float, default=200.0, help='m: a top this near a known summit is it')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    center = tuple(float(v) for v in a.center.split(','))
    g = Grid(a.work)
    z = g.array()
    t0 = time.time()
    tops, col, isle, hz, cz, farc = load_deaths(a.work)
    prom = np.where(col >= 0, hz - cz, hz)
    print(f'{len(tops)} deaths loaded in {time.time() - t0:.0f}s', flush=True)

    # the P100s in the circle: by their cells' rows and columns first, then by distance
    sel = np.flatnonzero(prom >= a.min_prominence)
    rows, cols = np.divmod(tops[sel], g.cols)
    lat0, lon0 = g.latlon(0, 0)
    lat = lat0 - rows / 3600.0
    lon = lon0 + cols / 3600.0
    d = np.hypot((lat - center[0]) * 110.574, (lon - center[1]) * 111.32 * math.cos(math.radians(center[0])))
    inside = sel[d <= a.radius]
    print(f'{len(sel)} tops of P >= {a.min_prominence:g} in all; {len(inside)} within {a.radius:g} km', flush=True)

    known = json.load(open(a.known))
    kgrid = {}
    for s in known:
        kgrid.setdefault((round(s['lat']), round(s['lon'])), []).append(s)

    def known_at(ll):
        c = [s for dy in (-1, 0, 1) for dx in (-1, 0, 1) for s in kgrid.get((round(ll[0]) + dy, round(ll[1]) + dx), [])]
        best = min(c, key=lambda s: km(ll, (s['lat'], s['lon'])), default=None)
        return best if best is not None and km(ll, (best['lat'], best['lon'])) * 1000 <= a.same else None

    at = lambda cell: list(g.latlon(*divmod(int(cell), g.cols)))
    todo = [int(tops[i]) for i in inside]
    why = {c: 'around' for c in todo}
    out = {}
    rounds = 0
    while todo:
        rounds += 1
        starts = np.array(todo, np.int64)
        parents = walk_parents(z, g.rows, g.cols, tops, col, isle, hz, cz, farc, starts)
        nxt = []
        for c, p in zip(todo, parents):
            i = int(np.searchsorted(tops, c))
            rec = dict(lat=at(c)[0], lon=at(c)[1], h=round(float(hz[i]), 2), prom=round(float(prom[i]), 2),
                       island=bool(col[i] < 0), why=why[c])
            if col[i] >= 0:
                rr, cc = divmod(int(col[i]), g.cols)
                rec.update(col=at(col[i]), col_h=round(float(cz[i]), 2), higher=at(isle[i]),
                           edge=bool(rr == 0 or rr == g.rows - 1 or cc == 0 or cc == g.cols - 1))
            k = known_at((rec['lat'], rec['lon']))
            if k:
                rec['db'] = dict(pk=k['pk'], name=k['name'], alt=k['alt'])
            if p >= 0:
                rec['parent'] = at(p)
                if int(p) not in out and int(p) not in why and known_at(tuple(rec['parent'])) is None:
                    why[int(p)] = 'parent'
                    nxt.append(int(p))
            out[c] = rec
        print(f'round {rounds}: {len(todo)} walked, {len(nxt)} parents to add', flush=True)
        todo = nxt
    res = list(out.values())
    json.dump(res, open(a.out, 'w'), indent=0)
    n_db = sum(1 for r in res if 'db' in r)
    print(f'{len(res)} tops: {sum(r["why"] == "around" for r in res)} around, {sum(r["why"] == "parent" for r in res)} '
          f'parents from further; {n_db} already in the database -> {a.out}')


if __name__ == '__main__':
    main()
