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
import heapq
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


# ------------------------------------------------------------------------- any terrain (terrain.py)

HIGHER, PROMINENT, WINDOW_EDGE, NO_DATA, TOO_BIG = range(5)


@njit(cache=True)
def flood_from(z, w, h, start, cutoff, forced, cap):
    """
    From the top `start`, down the ground around it, highest first, until something higher than
    the top is reached: (HIGHER, that cell, the lowest level gone down to, i.e. the col). Unless
    `forced`, gives up once the flood goes `cutoff` below the top without (PROMINENT, ...): the
    top's prominence is at least the cutoff. WINDOW_EDGE when the flood reaches the window's
    border (a larger window decides), NO_DATA when it reaches a cell without data (the ground
    beyond is unknown), TOO_BIG after `cap` cells.
    """
    top = z[start]
    seen = np.zeros(z.size, np.bool_)
    seen[start] = True
    heap = [(-top, start)]
    level = top
    n = 0
    while len(heap):
        nz, p = heapq.heappop(heap)
        zp = -nz
        if zp > top:
            return HIGHER, p, level
        if not forced and zp < top - cutoff:
            return PROMINENT, p, level
        if zp < level:
            level = zp
        y = p // w
        x = p - y * w
        if y == 0 or y == h - 1 or x == 0 or x == w - 1:
            return WINDOW_EDGE, p, level
        n += 1
        if n > cap:
            return TOO_BIG, p, level
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                q = (y + dy) * w + x + dx
                if seen[q]:
                    continue
                seen[q] = True
                if np.isnan(z[q]):
                    return NO_DATA, q, level
                heapq.heappush(heap, (-z[q], q))
    return PROMINENT, start, level


class Climb:
    """
    The climb from a nearest higher point to the summit its ground belongs to, over a frame of
    `terrain.py` (the model's 1" grid, or the lidar at 10 m): uphill to a top, and on across every
    top whose own key col dips under `cutoff` below it (a crag, a bump on a ridge); a top of at
    least the cutoff is the answer if `match(lon, lat, cells)` names a summit for it (it says which,
    and whether that one is higher than the summit climbed for), otherwise the climb goes on across
    that top's key col too, however deep. Never across a valley to a top that is merely near.
    """

    def __init__(self, frame, cutoff=10.0, size=256, max_size=4096, cap=4_000_000):
        self.frame, self.cutoff, self.size0, self.max_size, self.cap = frame, cutoff, size, max_size, cap
        self.win = None          # (gy0, gx0, h, w, z flat)

    def _window(self, gy, gx, size):
        """ A window of `size` cells a side holding (gy, gx) well inside; the current one if it does. """
        if self.win is not None:
            gy0, gx0, h, w, _ = self.win
            m = size // 4
            if h >= size and gy0 + m <= gy < gy0 + h - m and gx0 + m <= gx < gx0 + w - m:
                return self.win
        gy0, gx0 = gy - size // 2, gx - size // 2
        z = self.frame.window(gy0, gx0, size, size).astype(np.float32)
        self.win = (gy0, gx0, size, size, np.ascontiguousarray(z).ravel())
        return self.win

    def run(self, lon, lat, match):
        """
        From (lon, lat) up: (summit match, info) or (None, info); info has `status` ('ok', 'window',
        'no data', 'too big', 'steps') and `tops`: (lon, lat, height, burn) of every top of at least
        the cutoff crossed or ended on, the last the parent's; `burn` is the frame's record of a
        burned cell, (height, model height, label), or None.
        """
        gy, gx = self.frame.cell_of(lon, lat)
        size, forced = self.size0, False
        info = dict(status='ok', tops=[])
        for _ in range(400):
            gy0, gx0, h, w, z = self._window(gy, gx, size)
            p = (gy - gy0) * w + (gx - gx0)
            if not np.isfinite(z[p]):
                info['status'] = 'no data'
                return None, info
            cells = climb_to(z, w, h, p)
            ty, tx = cells // w, cells % w
            if ((ty == 0) | (ty == h - 1) | (tx == 0) | (tx == w - 1)).any():
                gy, gx = gy0 + int(ty[0]), gx0 + int(tx[0])
                size *= 2
                if size > self.max_size:
                    info['status'] = 'window'
                    return None, info
                continue
            top = int(cells[0])
            gy, gx = gy0 + top // w, gx0 + top % w
            status, q, level = flood_from(z, w, h, top, self.cutoff, forced, self.cap)
            if status == WINDOW_EDGE:
                size *= 2
                if size > self.max_size:
                    info['status'] = 'window'
                    return None, info
                continue
            if status == NO_DATA:
                info['status'] = 'no data'
                return None, info
            if status == TOO_BIG:
                info['status'] = 'too big'
                return None, info
            if status == HIGHER:
                gy, gx = gy0 + q // w, gx0 + q % w
                forced = False
                continue
            # a top of at least the cutoff: a catalogued summit, higher, or on across its key col
            tlon, tlat = self.frame.lonlat(np.array([gy]), np.array([gx]))
            b = self.frame.burned(gy, gx)
            info['tops'].append((float(tlon[0]), float(tlat[0]), float(z[top]), b))
            plat_cells = [(gy0 + int(c) // w, gx0 + int(c) % w) for c in cells]
            hit = match(float(tlon[0]), float(tlat[0]), plat_cells, self)
            if hit is not None:
                return hit, info
            forced = True
        info['status'] = 'steps'
        return None, info

    def ascend(self, lon, lat, dip=0.0, within=None, origin=None):
        """
        The top a walk uphill from (lon, lat) reaches: (height, lon, lat), None if no data there or
        the top is not inside a window of the first size. With `dip`, on across every col that
        dips less than that below the top (a model's noise: twin bumps of one summit) to higher
        ground within `within` m of `origin` (lon, lat).
        """
        from terrain import distances
        gy, gx = self.frame.cell_of(lon, lat)
        gy0, gx0, h, w, z = self._window(gy, gx, self.size0)
        p = (gy - gy0) * w + (gx - gx0)
        if not np.isfinite(z[p]):
            return None
        for _ in range(50):
            cells = climb_to(z, w, h, p)
            ty, tx = cells // w, cells % w
            if ((ty == 0) | (ty == h - 1) | (tx == 0) | (tx == w - 1)).any():
                return None
            top = int(cells[0])
            tlon, tlat = self.frame.lonlat(np.array([gy0 + top // w]), np.array([gx0 + top % w]))
            if dip <= 0:
                break
            status, q, level = flood_from(z, w, h, top, dip, False, 100000)
            if status != HIGHER:
                break
            qlon, qlat = self.frame.lonlat(np.array([gy0 + q // w]), np.array([gx0 + q % w]))
            if distances(origin[0], origin[1], qlon, qlat)[0] > within:
                break
            p = int(q)
        return float(z[top]), float(tlon[0]), float(tlat[0])

    def top_of(self, lon, lat):
        """ The top (global cells of its plateau) a climb from (lon, lat) reaches in the current
        window, or None outside it. """
        if self.win is None:
            return None
        gy, gx = self.frame.cell_of(lon, lat)
        gy0, gx0, h, w, z = self.win
        if not (0 < gy - gy0 < h - 1 and 0 < gx - gx0 < w - 1):
            return None
        p = (gy - gy0) * w + (gx - gx0)
        if not np.isfinite(z[p]):
            return None
        cells = climb_to(z, w, h, p)
        return {(gy0 + int(c) // w, gx0 + int(c) % w) for c in cells}


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
