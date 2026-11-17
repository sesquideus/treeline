"""
Every summit of prominence >= P in a lidar DTM, blind: nothing from the database.

    uv run --group lidar python lidar/prominence.py [--min-prominence 100] [--out DIR]

Three stages, each cached in --work so a rerun skips what is done:

1. **Pool.** The 1 m raster into cells of --cell metres, keeping each cell's highest pixel
   and where in the cell it is. A summit keeps its exact height and position. A saddle can
   only come out higher (a pixel path is never higher than the cells it crosses), so every
   prominence from the pooled grid is a lower bound.
2. **Flood.** Union-find over the pooled grid from the top down. When two components meet,
   the one with the lower summit dies: that cell is its key col, the survivor's summit its
   (divide-tree) parent. A component that reached the data's edge — the border, a hole —
   before dying may have a lower key col outside the data: flagged `outside`.
3. **Refine.** Each candidate's key col at 1 m: in a window around the pooled col, the
   highest point at which the two sides join, flooding from the top again. A window cuts
   paths off, so this can only be lower than the true saddle; the pooled height is never
   lower. Both are kept, and a gap between them is flagged.

Heights are ellipsoidal (ETRS89-h) in the source; reported in EGM2008 and Bpv too.
"""
import argparse
import csv
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import rasterio
from numba import njit
from pyproj import Transformer
from rasterio.windows import Window

SOURCE = os.environ.get('TREELINE_DMR5', '/mnt/media/treeline/DTM5/DTM5_0_etrs89-tm34_h/dtm5_0/dmr5_etrs89.tif')
NODATA = np.float32(-np.inf)


# ------------------------------------------------------------------------------- pooling

def pool_strip(args):
    path, (r_off, c_off, region_w), row0, rows, cell, width = args
    with rasterio.open(path) as d:
        z = d.read(1, window=Window(c_off, r_off + row0, region_w, rows), boundless=True,
                   fill_value=d.nodata)
    z[~np.isfinite(z) | (z > 1e30)] = NODATA        # the file's nodata is 3.4e38
    pad = width * cell - z.shape[1]
    if pad:
        z = np.pad(z, ((0, 0), (0, pad)), constant_values=NODATA)
    blocks = z.reshape(rows // cell, cell, width, cell).transpose(0, 2, 1, 3).reshape(rows // cell, width, cell * cell)
    at = blocks.argmax(axis=2).astype(np.uint16)
    top = np.take_along_axis(blocks, at[..., None].astype(np.int64), axis=2)[..., 0]
    return row0, top, at


def pool(path, work, cell, workers, region):
    tag = f'{cell}' + ('' if region is None else '-' + '-'.join(map(str, region)))
    zf, af = os.path.join(work, f'pooled{tag}.npy'), os.path.join(work, f'argmax{tag}.npy')
    if os.path.exists(zf) and os.path.exists(af):
        return np.load(zf, mmap_mode='r'), np.load(af, mmap_mode='r')
    with rasterio.open(path) as d:
        r_off, c_off, H, W = region or (0, 0, d.height, d.width)
    h, w = -(-H // cell), -(-W // cell)
    top = np.lib.format.open_memmap(zf + '.part', 'w+', np.float32, (h, w))
    at = np.lib.format.open_memmap(af + '.part', 'w+', np.uint16, (h, w))
    strip = cell * 50
    jobs = [(path, (r_off, c_off, W), r, strip, cell, w) for r in range(0, h * cell, strip)]
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        for k, (row0, t, a) in enumerate(ex.map(pool_strip, jobs)):
            r = row0 // cell
            n = min(t.shape[0], h - r)
            top[r:r + n], at[r:r + n] = t[:n], a[:n]
            if k % 50 == 0:
                print(f'  pooled {k}/{len(jobs)} strips, {time.time() - t0:.0f}s', flush=True)
    top.flush(), at.flush()
    del top, at
    os.rename(zf + '.part', zf), os.rename(af + '.part', af)
    return np.load(zf, mmap_mode='r'), np.load(af, mmap_mode='r')


# ------------------------------------------------------------------------------- flooding

@njit(cache=True)
def find(parent, i):
    root = i
    while parent[root] != root:
        root = parent[root]
    while parent[i] != root:
        nxt = parent[i]
        parent[i] = root
        i = nxt
    return root


@njit(cache=True)
def flood(z, order, w, h, keep_below):
    """
    `keep_below`: a dying summit is recorded only if its prominence is at least this.
    Returns, per recorded death: summit, col, a cell next to the col on the dying side and
    one on the survivor's, the survivor's summit, and whether the dying side reached an edge.
    """
    n = w * h
    parent = np.full(n, -1, np.int32)
    peak = np.full(n, -1, np.int32)
    edge = np.zeros(n, np.bool_)
    edge_h = np.full(n, -np.inf, np.float32)   # per root: the highest height it touched an edge at
    cap = 1 << 20
    o_peak = np.empty(cap, np.int64)
    o_col = np.empty(cap, np.int64)
    o_sd = np.empty(cap, np.int64)
    o_sb = np.empty(cap, np.int64)
    o_parent = np.empty(cap, np.int64)
    o_edge = np.empty(cap, np.bool_)
    o_edge_h = np.empty(cap, np.float32)
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
        on_edge = False
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dy == 0 and dx == 0:
                    continue
                yy = y + dy
                xx = x + dx
                if yy < 0 or yy >= h or xx < 0 or xx >= w:
                    on_edge = True
                    continue
                q = yy * w + xx
                if z[q] == -np.inf:
                    on_edge = True
                    continue
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
            edge[p] = on_edge
            if on_edge:
                edge_h[p] = z[p]
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
            if z[peak[r]] - z[p] >= keep_below and c < cap:
                o_peak[c] = peak[r]
                o_col[c] = p
                o_sd[c] = via[j]
                o_sb[c] = via[best]
                o_parent[c] = peak[b]
                o_edge[c] = edge[r]
                o_edge_h[c] = edge_h[r]
                c += 1
            parent[r] = b
            if edge[r]:
                edge[b] = True
                edge_h[b] = max(edge_h[b], edge_h[r])
        parent[p] = b
        if on_edge:
            if not edge[b]:
                edge_h[b] = z[p]
            edge[b] = True
    # what never died: the top, and any island of data cut off from it
    rest = []
    for p in range(n):
        if parent[p] == p:
            rest.append(peak[p])
    return (o_peak[:c], o_col[:c], o_sd[:c], o_sb[:c], o_parent[:c], o_edge[:c], o_edge_h[:c],
            np.array(rest, np.int64))


@njit(cache=True)
def join_height(z, w, h, a, b):
    """Flood a window from the top; the height at which pixels `a` and `b` first connect."""
    n = w * h
    order = np.argsort(-z.ravel())
    zr = z.ravel()
    parent = np.full(n, -1, np.int64)
    for k in range(n):
        p = order[k]
        if zr[p] == -np.inf:
            break
        parent[p] = p
        y = p // w
        x = p - y * w
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                yy = y + dy
                xx = x + dx
                if (dy == 0 and dx == 0) or yy < 0 or yy >= h or xx < 0 or xx >= w:
                    continue
                q = yy * w + xx
                if parent[q] >= 0:
                    rq = find(parent, q)
                    rp = find(parent, p)
                    if rq != rp:
                        parent[rq] = rp
        if parent[a] >= 0 and parent[b] >= 0 and find(parent, a) == find(parent, b):
            return zr[p], p
    return -np.inf, -1


# ------------------------------------------------------------------------------- beyond the lidar

GLO30 = os.path.expanduser('~/.cache/treeline/lidar/glo30')


class Grid:
    """
    The pooled lidar, padded by `pad` cells on every side, its holes and the padding filled
    from Copernicus GLO-30 where that is available. `filled` marks those cells: they have no
    1 m pixel behind them, and their heights are a 30 m *surface* model — treetops, not
    ground — so less precise in every way.
    """

    def __init__(self, top, at, cell, pad, offset, transform):
        self.at, self.cell, self.pad, self.offset, self.transform = at, cell, pad, offset, transform
        self.h, self.w = top.shape
        self.H, self.W = self.h + 2 * pad, self.w + 2 * pad
        self.z = np.full((self.H, self.W), NODATA, np.float32)
        self.z[pad:pad + self.h, pad:pad + self.w] = top
        self.filled = np.zeros((self.H, self.W), np.bool_)

    def fill(self, tiles, work, tag):
        zf, ff = os.path.join(work, f'ext{tag}.npy'), os.path.join(work, f'extfilled{tag}.npy')
        if os.path.exists(zf) and os.path.exists(ff):
            self.z, self.filled = np.load(zf), np.load(ff)
            return
        from affine import Affine
        from rasterio.warp import Resampling, reproject
        from scipy.ndimage import map_coordinates
        dst = self.transform * Affine.translation(self.offset[1] - self.pad * self.cell,
                                                  self.offset[0] - self.pad * self.cell) \
            * Affine.scale(self.cell)
        glo = np.full((self.H, self.W), np.nan, np.float32)
        for path in sorted(tiles):
            with rasterio.open(path) as t:
                reproject(rasterio.band(t, 1), glo, dst_transform=dst, dst_crs='EPSG:3046',
                          dst_nodata=np.nan, init_dest_nodata=False, resampling=Resampling.bilinear)
        # EGM2008 to ellipsoidal: N from a 1 km lattice, interpolated — it is smooth.
        step = 100
        rr, cc = np.arange(0, self.H + step, step), np.arange(0, self.W + step, step)
        e, n = dst * np.meshgrid(cc + 0.5, rr + 0.5)
        lon, lat = Transformer.from_crs(3046, 4326, always_xy=True).transform(e, n)
        undulation = (Transformer.from_crs('EPSG:4326+3855', 'EPSG:4937', always_xy=True)
                      .transform(lon, lat, np.zeros_like(lon))[2]).astype(np.float32)
        for r0 in range(0, self.H, 1000):
            r1 = min(r0 + 1000, self.H)
            holes = ~np.isfinite(self.z[r0:r1]) & np.isfinite(glo[r0:r1])
            if not holes.any():
                continue
            ys, xs = np.nonzero(holes)
            nn = map_coordinates(undulation, [(ys + r0) / step, xs / step], order=1)
            self.z[r0:r1][holes] = glo[r0:r1][holes] + nn
            self.filled[r0:r1] |= holes
        del glo
        np.save(zf, self.z), np.save(ff, self.filled)

    def is_filled(self, index):
        r, c = divmod(int(index), self.W)
        return bool(self.filled[r, c]) or not (self.pad <= r < self.pad + self.h and self.pad <= c < self.pad + self.w)

    def pixel(self, index):
        """Source-raster (row, col) of the cell's highest 1 m pixel; of its centre if filled."""
        r, c = divmod(int(index), self.W)
        r, c = r - self.pad, c - self.pad
        if self.is_filled(index):
            return (r * self.cell + self.cell // 2 + self.offset[0],
                    c * self.cell + self.cell // 2 + self.offset[1])
        o = int(self.at[r, c])
        return (r * self.cell + o // self.cell + self.offset[0],
                c * self.cell + o % self.cell + self.offset[1])


# ------------------------------------------------------------------------------- refining

def refine_col(d, grid, col, side_d, side_b, radius):
    """None when any of the three cells is not lidar: there is no 1 m data to refine with."""
    if any(grid.is_filled(i) for i in (col, side_d, side_b)):
        return None
    cr, cc = grid.pixel(col)
    r0, c0 = cr - radius, cc - radius
    z = d.read(1, window=Window(c0, r0, 2 * radius + 1, 2 * radius + 1), boundless=True,
               fill_value=d.nodata)
    z = np.where(~np.isfinite(z) | (z > 1e30), NODATA, z).astype(np.float32)
    seeds = []
    for s in (side_d, side_b):
        sr, sc = grid.pixel(s)
        seeds.append((sr - r0) * z.shape[1] + (sc - c0))
    height, p = join_height(z, z.shape[1], z.shape[0], seeds[0], seeds[1])
    if p < 0:
        return None
    pr, pc = divmod(int(p), z.shape[1])
    border = pr in (0, z.shape[0] - 1) or pc in (0, z.shape[1] - 1)
    return float(height), (r0 + pr, c0 + pc), border


def run(grid, shift, keep_below):
    """
    One flood over the grid, its filled cells moved by `shift` metres — or left out, with
    `shift=None`: the lidar alone. The deaths of lidar summits only, by summit cell; and
    what never died.
    """
    flat = grid.z.ravel()
    filled = np.flatnonzero(grid.filled.ravel())
    saved = None
    if shift is None:
        saved = flat[filled].copy()
        flat[filled] = NODATA
    elif shift:
        flat[filled] += np.float32(shift)
    try:
        t = time.time()
        order = np.argsort(-flat, kind='stable').astype(np.int32)
        peaks, cols, sds, sbs, parents, edges, edge_hs, rest = flood(flat, order, grid.W, grid.H, keep_below)
        del order
        deaths = {}
        for i in range(len(peaks)):
            if grid.is_filled(peaks[i]):
                continue
            deaths[int(peaks[i])] = dict(col=int(cols[i]), sd=int(sds[i]), sb=int(sbs[i]),
                                         parent=int(parents[i]), edge=bool(edges[i]),
                                         edge_h=float(edge_hs[i]), col_h=float(flat[cols[i]]),
                                         parent_h=float(flat[parents[i]]))
        rest = [int(p) for p in rest if flat[p] > -np.inf and not grid.is_filled(p)]
        what = 'lidar alone' if shift is None else f'filled cells {shift:+g} m'
        print(f'  flood, {what}: {len(deaths)} candidates in {time.time() - t:.0f}s', flush=True)
        return deaths, rest
    finally:
        if saved is not None:
            flat[filled] = saved
        elif shift:
            flat[filled] -= np.float32(shift)


# ------------------------------------------------------------------------------- main

FILLED_NOTE = 'Copernicus GLO-30 (30 m, a surface model with the forest canopy in it, less precise than lidar)'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', default=SOURCE)
    ap.add_argument('--min-prominence', type=float, default=100.0)
    ap.add_argument('--margin', type=float, default=20.0,
                    help='pooled prominence is a lower bound: refine everything down to P - margin')
    ap.add_argument('--cell', type=int, default=10)
    ap.add_argument('--radius', type=int, default=150, help='1 m refinement window half-size')
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    ap.add_argument('--out', default='.')
    ap.add_argument('--window', type=int, nargs=4, metavar=('ROW', 'COL', 'HEIGHT', 'WIDTH'),
                    help='only this part of the raster, in source pixels (for trying it out)')
    ap.add_argument('--fill', default=GLO30,
                    help='directory of Copernicus GLO-30 tiles to fill beyond the lidar with; "" for none')
    ap.add_argument('--pad-km', type=float, default=20.0, help='how far beyond the raster to fill')
    ap.add_argument('--canopy', type=float, default=25.0,
                    help='second run with filled cells this much lower: GLO-30 sees treetops')
    args = ap.parse_args()
    os.makedirs(args.work, exist_ok=True)
    thr = args.min_prominence

    t0 = time.time()
    region = tuple(args.window) if args.window else None
    offset = region[:2] if region else (0, 0)
    top, at = pool(args.source, args.work, args.cell, args.workers, region)
    print(f'pooled grid {top.shape[1]}x{top.shape[0]} in {time.time() - t0:.0f}s', flush=True)

    import glob
    tiles = glob.glob(os.path.join(args.fill, '*.tif')) if args.fill else []
    pad = int(args.pad_km * 1000 / args.cell) if tiles else 0
    with rasterio.open(args.source) as d:
        grid = Grid(np.asarray(top), at, args.cell, pad, offset, d.transform)
    if tiles:
        t1 = time.time()
        tag = f'{args.cell}-pad{pad}' + ('' if region is None else '-' + '-'.join(map(str, region)))
        grid.fill(tiles, args.work, tag)
        print(f'filled {grid.filled.sum()} cells from {len(tiles)} GLO-30 tiles in {time.time() - t1:.0f}s', flush=True)

    keep = thr - args.margin
    # The lidar alone decides what is a candidate: more terrain can only raise a key col.
    alone, rest = run(grid, None, keep)
    primary = run(grid, 0.0, keep)[0] if tiles else alone
    discounted = run(grid, -args.canopy, keep)[0] if tiles else alone
    flat = grid.z.ravel()

    with rasterio.open(args.source) as d:
        to_ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
        bpv = Transformer.from_crs('EPSG:4937', 'EPSG:4258+8357', always_xy=True, allow_ballpark=False)
        egm = Transformer.from_crs('EPSG:4937', 'EPSG:4326+3855', always_xy=True, allow_ballpark=False)

        def place(rc, height):
            e, n = d.transform * (rc[1] + 0.5, rc[0] + 0.5)
            lon, lat = to_ll.transform(e, n)

            def converted(t):
                v = t.transform(lon, lat, height)[2]
                return round(v, 2) if np.isfinite(v) else None     # outside the datum's grid
            return dict(lat=round(lat, 6), lon=round(lon, 6), h=round(height, 2),
                        egm2008=converted(egm), bpv=converted(bpv))

        refined = {}

        def key_col(death):
            """(height, place, exact): at 1 m where the col and both sides are lidar."""
            k = (death['col'], death['sd'], death['sb'])
            if k not in refined:
                r = refine_col(d, grid, *k, args.radius)
                if r is None:
                    refined[k] = (death['col_h'], place(grid.pixel(death['col']), death['col_h']), False, False)
                else:
                    refined[k] = (r[0], place(r[1], r[0]), True, r[2])
            return refined[k]

        def summit(index, shift=0.0):
            h = float(flat[index])
            return place(grid.pixel(index), h)

        rows, rejected = [], []
        t1 = time.time()
        below = f'< {keep:g}'
        for index in sorted(alone):
            peak = summit(index)
            p0 = peak['h'] - key_col(alone[index])[0]
            if p0 < thr:
                continue
            a, b = primary.get(index), discounted.get(index)
            pa = peak['h'] - key_col(a)[0] if a else None
            pb = peak['h'] - key_col(b)[0] if b else None
            # The discounted run lowers the outside: its prominence is the higher one.
            best = max((p for p in (pa, pb) if p is not None), default=-np.inf)
            row = dict(summit=peak, prominence_lidar=round(p0, 2),
                       prominence=round(pa, 2) if pa is not None else below,
                       prominence_discounted=round(pb, 2) if pb is not None else below, flags=[])
            if best < thr:
                row.update(col=None, prominence_pooled=None, at_least=None, parent=None)
                row['flags'].append(f'P >= {thr:g} on the lidar alone, not with {FILLED_NOTE}: '
                                    f'the key col is beyond the lidar, or the summit is a shoulder '
                                    f'of a higher top across the border')
                if b:
                    other = key_col(b)[1]
                    row['flags'].append(f'key col with the canopy discounted: {other["lat"]}, {other["lon"]}')
                rejected.append(row)
                continue
            death = a or b
            col_h, col, exact, border = key_col(death)
            row.update(col=col, prominence_pooled=round(peak['h'] - death['col_h'], 2),
                       parent=summit(death['parent']), at_least=None)
            flags = row['flags']
            if pa is None or pa < thr:
                flags.append(f'P >= {thr:g} only if the Copernicus canopy is discounted')
            if not exact and grid.is_filled(death['col']):
                flags.append(f'key col outside the lidar, in {FILLED_NOTE}')
            elif not exact:
                flags.append('key col not refined at 1 m (a side of it is outside the lidar)')
            if border:
                flags.append('refined col on window border')
            if a and b and a['col'] != b['col']:
                other = key_col(b)[1]
                flags.append(f'with the canopy discounted the key col moves to {other["lat"]}, {other["lon"]}'
                             f' (P {pb:.1f})')
            if grid.is_filled(death['parent']):
                flags.append('parent outside the lidar (Copernicus)')
            # Any way out of the filled area crosses its edge no higher than the highest point
            # at which this summit's side reached it.
            if death['edge']:
                lowest = round(peak['h'] - max(col_h, death['edge_h']), 2)
                row['at_least'] = lowest
                if lowest < thr:
                    flags.append('key col may lie beyond all the data' + (', even Copernicus' if tiles else ''))
            rows.append(row)
        roots = [dict(summit=summit(p), col=None, prominence=None, prominence_discounted=None, prominence_lidar=None,
                      prominence_pooled=None, at_least=None, parent=None,
                      flags=['highest in the data: key col beyond it'])
                 for p in rest]
        print(f'refined in {time.time() - t1:.0f}s', flush=True)

    rows.sort(key=lambda r: -r['summit']['h'])
    rows = roots + rows
    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f'p{thr:g}')
    write_csv(stem + '.csv', rows)
    write_csv(stem + '-rejected.csv', sorted(rejected, key=lambda r: -r['summit']['h']))
    features = []
    for k, r in enumerate(rows, 1):
        s = r['summit']
        features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [s['lon'], s['lat']]},
                         'properties': {'rank': k, 'kind': 'summit', **r, 'summit': None}})
        if r['col']:
            c = r['col']
            features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [c['lon'], c['lat']]},
                             'properties': {'rank': k, 'kind': 'col', 'h': c['h'], 'egm2008': c['egm2008']}})
    json.dump({'type': 'FeatureCollection', 'features': features}, open(stem + '.geojson', 'w'))
    # Waypoints for Freemap (Map > Open file): each summit and its key col.
    from xml.sax.saxutils import escape
    with open(stem + '.gpx', 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<gpx version="1.1" creator="treeline lidar" xmlns="http://www.topografix.com/GPX/1/1">\n')
        for k, r in enumerate(rows, 1):
            s = r['summit']
            p = next((x for x in (r['prominence'], r['prominence_discounted']) if isinstance(x, float)), None)
            prom = f"P{p:.0f}" if p is not None else 'top'
            alt = s['bpv'] if s['bpv'] is not None else s['egm2008']
            f.write(f'<wpt lat="{s["lat"]}" lon="{s["lon"]}"><ele>{alt}</ele>'
                    f'<name>#{k} {alt:.1f} {prom}</name>'
                    f'<desc>{escape("; ".join(r["flags"]))}</desc><sym>Summit</sym></wpt>\n')
            if r['col']:
                c = r['col']
                alt = c['bpv'] if c['bpv'] is not None else c['egm2008']
                f.write(f'<wpt lat="{c["lat"]}" lon="{c["lon"]}"><ele>{alt}</ele>'
                        f'<name>#{k} col {alt:.1f}</name><sym>Crossing</sym></wpt>\n')
        f.write('</gpx>\n')
    print(f'{len(rows)} summits with P >= {thr:g} -> {stem}.csv/.geojson/.gpx, {len(rejected)} rejected '
          f'({time.time() - t0:.0f}s total)')


def write_csv(path, rows):
    with open(path, 'w', newline='') as f:
        out = csv.writer(f)
        out.writerow(['rank', 'lat', 'lon', 'alt_egm2008', 'alt_bpv', 'prominence', 'prominence_lidar_alone',
                      'prominence_canopy_discounted', 'prominence_pooled', 'prominence_at_least',
                      'col_lat', 'col_lon', 'col_egm2008', 'col_bpv',
                      'parent_lat', 'parent_lon', 'parent_egm2008', 'flags'])
        for k, r in enumerate(rows, 1):
            s, c, p = r['summit'], r['col'] or {}, r['parent'] or {}
            out.writerow([k, s['lat'], s['lon'], s['egm2008'], s['bpv'], r['prominence'], r['prominence_lidar'],
                          r['prominence_discounted'], r['prominence_pooled'], r['at_least'],
                          c.get('lat'), c.get('lon'), c.get('egm2008'), c.get('bpv'),
                          p.get('lat'), p.get('lon'), p.get('egm2008'), '; '.join(r['flags'])])


if __name__ == '__main__':
    main()
