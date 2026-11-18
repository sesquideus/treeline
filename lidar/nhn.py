"""
The nearest higher neighbour by climbing: from a summit's nearest higher point, uphill along
connected ground to the first top higher than the summit with prominence >= --cutoff (10 m).
Smaller tops on the way — crags, bumps on a ridge — are climbed past: across each one's own key
col, a dip of under --cutoff, and on up. Never across a valley to a top that is merely near.

    uv run --group lidar python lidar/nhn.py DB.json ISOLATION.json OSM.json OUT.json

DB.json from export_db.py, ISOLATION.json from isolation.py (the nearest higher points),
OSM.json the OSM peaks. Writes a changeset: the tops not yet in the database that some summit's
climb ends on (each with its key col at 1 m, its parent, an OSM name if one is near), and the
isolations — nearest higher point and neighbour — for the summits whose isolation came from the
lidar or was missing, and for the added tops themselves (whose climbs may add more, until none
does). Isolations measured by hand are left alone.
"""
import argparse
import json
import math
import os
import sys

import numpy as np
import rasterio
from numba import njit
from pyproj import Transformer
from rasterio.windows import Window

from compare import Osm
from isolation import CELL, nearest_higher, pyramid
from parents import DivideTree
from prominence import SOURCE, Grid, pool, refine_col


@njit(cache=True)
def climb_to(z, w, h, p):
    """Steepest ascent from cell p, across flat ground, to a top; the plateau's cells if flat."""
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
        if best >= 0:
            p = best
            continue
        seen = {p: True}
        queue = [p]
        found = -1
        i = 0
        while i < len(queue) and found < 0:
            c = queue[i]
            i += 1
            cy = c // w
            cx = c - cy * w
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    yy = cy + dy
                    xx = cx + dx
                    if (dy == 0 and dx == 0) or yy < 0 or yy >= h or xx < 0 or xx >= w:
                        continue
                    q = yy * w + xx
                    if z[q] > z[p]:
                        found = q
                    elif z[q] == z[p] and q not in seen:
                        seen[q] = True
                        queue.append(q)
        if found < 0:
            return np.array(queue, dtype=np.int64)
        p = found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db')
    ap.add_argument('isolation')
    ap.add_argument('osm')
    ap.add_argument('out')
    ap.add_argument('--cutoff', type=float, default=10.0)
    ap.add_argument('--matches', help='lidar summits matched to database ones (JSON [{lidar: [lat, lon], pk}])')
    ap.add_argument('--held', help='candidates held back on purpose (the changeset generator\'s -held.json)')
    ap.add_argument('--first-new', type=int, default=3135,
                    help='the first summit pk this tooling created: from there on, by position')
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    args = ap.parse_args()
    summits = json.load(open(args.db))['summits']
    iso = {x['pk']: x for x in json.load(open(args.isolation)) if x.get('nhp_px')}
    osm = Osm(args.osm)

    top, at = pool(SOURCE, args.work, CELL, 8, None)
    top = np.asarray(top)
    H, W = top.shape
    flat = np.ascontiguousarray(top).ravel()
    levels = pyramid(top)
    tree = DivideTree(flat, W, H)
    print(f'tree of {len(tree.nodes)} peaks', flush=True)
    nodeset = set(tree.nodes.tolist())

    d = rasterio.open(SOURCE)
    grid = Grid(np.zeros((1, 1), np.float32), at, CELL, 0, (0, 0), d.transform)
    grid.h, grid.w, grid.W = H, W, W
    grid.filled = np.zeros((H, W), np.bool_)
    tm = Transformer.from_crs(4326, d.crs, always_xy=True)
    ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
    bpv = Transformer.from_crs('EPSG:4937', 'EPSG:4258+8357', always_xy=True)
    inv = ~d.transform

    def lonlat_px(r, c):
        e, n = d.transform * (c + 0.5, r + 0.5)
        lon, lat = ll.transform(e, n)
        return round(lat, 6), round(lon, 6)

    def peak_of(cell):
        """The tree's node for the top a climb from `cell` reaches."""
        cells = climb_to(flat, W, H, int(cell))
        for c in cells:
            if int(c) in nodeset:
                return int(c)
        return int(cells[0])

    # catalogued summits by the 10 m cells around their point
    by_cell = {}
    for s in summits:
        c, r = inv * tm.transform(s['point']['lon'], s['point']['lat'])
        R, C = int(r) // CELL, int(c) // CELL
        for dr in range(-6, 7):            # within 60 m: a database point is not always on its top
            for dc in range(-6, 7):
                by_cell.setdefault((R + dr) * W + C + dc, []).append(s)

    def cell_of(lat, lon):
        c, r = inv * tm.transform(lon, lat)
        return (int(r) // CELL) * W + int(c) // CELL

    # Tops the P30 comparison already matched to a database summit (within 150 m), and tops held
    # back on purpose (outside Slovakia, a key col perhaps beyond the data, a possible duplicate):
    # a climb never adds those, it climbs on past them.
    by_pk = {s['pk']: s for s in summits}
    matched = {cell_of(*m['lidar']): by_pk[m['pk']] for m in (json.load(open(args.matches)) if args.matches else [])
               if m['pk'] in by_pk}
    held = {cell_of(h['lat'], h['lon']) for h in (json.load(open(args.held)) if args.held else [])}

    def near(cell, where):
        """`cell` or one beside it in `where` (a top and its match can fall in neighbouring cells)."""
        r, c = divmod(int(cell), W)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                k = (r + dr) * W + c + dc
                if k in where:
                    return where[k] if isinstance(where, dict) else True
        return None

    def catalogued(cell):
        m = near(cell, matched)
        if m:
            return m
        hits = by_cell.get(int(cell), [])
        return max(hits, key=lambda s: s['point']['alt']) if hits else None

    def climb(nhp_px, h, alt):
        """
        From the NHP up to the first top higher than the summit (h on the lidar, `alt` in the
        database) with pooled prominence >= cutoff. A top already catalogued counts only if the
        database has it higher too — the summit's own height there can be above its lidar top (a
        shaved crag); otherwise the climb goes on, instead of adding the top a second time.
        """
        q = peak_of((nhp_px[0] // CELL) * W + nhp_px[1] // CELL)
        for _ in range(100000):
            if q < 0:
                return -1
            if flat[q] > h and tree.prom[q] >= args.cutoff and not near(q, held):
                hit = catalogued(q)
                if hit is None or hit['point']['alt'] > alt:
                    return q
            q = tree.far.get(q, -1)
        return -1

    new_tops = {}          # cell -> record

    def add_top(cell):
        """A top some summit's climb ends on, not in the database: summit, key col, parent, name."""
        if cell in new_tops:
            return
        r, c = grid.pixel(cell)
        lat, lon = lonlat_px(r, c)
        hz = float(flat[cell])
        rec = dict(key=f'lidar:{lat:.6f},{lon:.6f}', lat=lat, lon=lon, top_px=(int(r), int(c)), h=hz,
                   altitude=round(bpv.transform(lon, lat, hz)[2], 2), datum='Bpv', countries=['sk'],
                   prominence=round(float(tree.prom[cell]), 1), notes=[])
        e = tree.edge_of.get(int(cell))
        if e is None:
            return
        fine = refine_col(d, grid, int(tree.col[e]), int(tree.sd[e]), int(tree.sb[e]), 150)
        col_h, col_rc = (fine[0], fine[1]) if fine else (float(flat[tree.col[e]]), grid.pixel(tree.col[e]))
        clat, clon = lonlat_px(*col_rc)
        rec['col'] = dict(lat=clat, lon=clon, altitude=round(bpv.transform(clon, clat, col_h)[2], 2),
                          datum='Bpv', countries=['sk'], name=None, names=[], notes=[])
        rec['prominence'] = round(hz - col_h, 2)
        # parent: up the walk to the first catalogued summit higher and more prominent
        p, parent = int(cell), None
        for _ in range(1000):
            p = tree.parent(p)
            if p < 0:
                break
            s = catalogued(p)
            if s and s['point']['alt'] > rec['altitude'] and (s['prominence'] or 0) > rec['prominence']:
                parent = ref(s)
                break
        rec['parent'] = parent
        o = osm.nearest((lat, lon), ['peak', 'volcano', 'hill'], 60)
        rec['name'] = o['name'] if o and o['name'] else None
        rec['names'] = ([dict(lang='sk', name=o['name'], local=True)] if rec['name'] else [])
        new_tops[cell] = rec

    def ref(s):
        # Summits this tooling made have no pk in common with the server: by position.
        if s['pk'] >= args.first_new:
            return dict(at=[s['point']['lat'], s['point']['lon']])
        return dict(pk=s['pk'], name=s['point']['name'])

    isolations, unresolved = [], []
    queue = []
    for s in summits:
        x = iso.get(s['pk'])
        if x is None or x['beyond_data']:
            continue
        if s.get('nhp') is not None and s.get('isolation_source') not in (None, 'DMR 5.0'):
            continue            # measured by hand: left alone
        queue.append(('db', s, x['nhp_px'], x['top_h'], x['nhp'], x['isolation_m']))
    k = 0
    while k < len(queue):
        kind, s, nhp_px, h, nhp, metres = queue[k]
        k += 1
        q = climb(nhp_px, h, s['altitude'] if kind == 'new' else s['point']['alt'])
        if q < 0:
            unresolved.append(s['key'] if kind == 'new' else s['pk'])
            continue
        hit = catalogued(q)
        if hit is None or hit['point']['alt'] <= (s['altitude'] if kind == 'new' else s['point']['alt']):
            add_top(q)
            if q not in new_tops:
                unresolved.append(s['key'] if kind == 'new' else s['pk'])
                continue
            parent = dict(new=new_tops[q]['key'])
            # the new top needs its own isolation: its NHP now, its climb later in the queue
            t = new_tops[q]
            if 'queued' not in t:
                t['queued'] = True
                best, best_at, beyond = nearest_higher(d, levels, t['top_px'][0], t['top_px'][1], t['h'])
                if best_at is not None and not beyond:
                    queue.append(('new', t, best_at, t['h'], lonlat_px(*best_at), best))
        else:
            parent = ref(hit)
        who = dict(new=s['key']) if kind == 'new' else ref(s)
        isolations.append(dict(summit=who, parent=parent, nhp=list(nhp), metres=round(metres, 1)))
        if k % 500 == 0:
            print(f'{k}/{len(queue)} climbed, {len(new_tops)} tops to add', flush=True)

    for t in new_tops.values():
        for f in ('top_px', 'h', 'queued'):
            t.pop(f, None)
    json.dump(dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
                   summits=sorted(new_tops.values(), key=lambda t: -t['prominence']),
                   isolations=isolations), open(args.out, 'w'), ensure_ascii=False, indent=1)
    print(f'{len(isolations)} isolations, {len(new_tops)} tops to add, {len(unresolved)} unresolved -> {args.out}')


if __name__ == '__main__':
    main()
