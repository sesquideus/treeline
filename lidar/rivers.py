"""
Rivers from a DEM, named from OpenStreetMap, in TreeLine's river terms: a source, a mouth, the
parent river it joins and the bank it joins from.

    cd lidar && uv run --project .. --group lidar python rivers.py --bbox S,N,W,E \
        --osm OSM.json [--lakes LAKES.json] [--known TREELINE.json] --out RIVERS.json \
        [--min-area 1] [--river-area 5] [--lake-area 0.1]

1. Drainage: FABDEM at 1″ for the box, lowered --burn metres along OSM's named rivers and
   streams (a 30 m model smooths away a stream's bed on a fan or a floodplain, and keeps the
   embankments that cross it), depressions filled by priority flood with a tiny
   gradient across flats (Barnes et al. 2014), so every cell drains; flow to the steepest of
   the eight neighbours; catchment area accumulated downstream.
2. Water bodies (--lakes, at least --lake-area km²): flat, at their median level, and each
   drains through one outlet (its lowest edge, the dam) along a tree through the water. In the
   network a water body is one node: one river passes through it, along its flow path to the
   outlet, and every other stream entering it is a tributary of that river there, its bank
   from the shore it comes from relative to the river through the water.
3. Network: cells draining at least --min-area km² are channel. From each outlet upstream, a
   river continues up
   - the branch its TreeLine source drains through, where TreeLine knows the river (--known:
     the source's own flow path; the river then ends at TreeLine's spring). Which TreeLine
     river a mouth starts: TreeLine's mouth within 500 m, else a name read up the river;
   - else the branch carrying its name, when the larger branch clearly reads another;
   - else the branch with the larger catchment.
   Every other branch is a tributary, its bank from the angle at the confluence (left and
   right facing downstream). Rivers draining less than --river-area km² are left out.
4. Names: each river takes the OpenStreetMap waterway name its course follows for most of its
   length (within 150 m; a tributary's first ~180 m do not count, its parent's line is near;
   under 20 % of its course, no name). Its source is TreeLine's spring where it followed one,
   else the highest point OSM still maps under its name, else the DEM's channel head.
5. Output: per river its key, name, TreeLine river followed, course (mouth to source, the 30 m
   path to within 10 m), source and mouth (EGM2008 heights from the DEM; a mouth in a water body at its
   level), parent (key), mouth side, the water body it joins in and those it passes through,
   catchment, length, the highest point of its basin, OSM cover, and how it ends (`sea`, `sink`,
   `edge` of the box: the real mouth lies beyond). The water bodies with their outlines.

OSM.json: Overpass `out geom` of named waterways (river, stream, canal) in the box. LAKES.json:
Overpass `out geom` of natural=water and landuse=reservoir. TREELINE.json: the database's rivers
([{pk, name, source, mouth, side, parent}]).
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np
from numba import njit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from catchments import ARC, fabdem_reader  # noqa: E402

DY = np.array([-1, -1, -1, 0, 0, 1, 1, 1], np.int64)
DX = np.array([-1, 0, 1, -1, 1, -1, 0, 1], np.int64)


@njit(cache=True)
def _push(hz, hi, n, z, i):
    hz[n] = z
    hi[n] = i
    c = n
    while c > 0:
        p = (c - 1) // 2
        if hz[p] < hz[c] or (hz[p] == hz[c] and hi[p] <= hi[c]):
            break
        hz[p], hz[c] = hz[c], hz[p]
        hi[p], hi[c] = hi[c], hi[p]
        c = p
    return n + 1


@njit(cache=True)
def _pop(hz, hi, n):
    z, i = hz[0], hi[0]
    n -= 1
    hz[0], hi[0] = hz[n], hi[n]
    c = 0
    while True:
        l, r, m = 2 * c + 1, 2 * c + 2, c
        if l < n and (hz[l] < hz[m] or (hz[l] == hz[m] and hi[l] < hi[m])):
            m = l
        if r < n and (hz[r] < hz[m] or (hz[r] == hz[m] and hi[r] < hi[m])):
            m = r
        if m == c:
            break
        hz[m], hz[c] = hz[c], hz[m]
        hi[m], hi[c] = hi[c], hi[m]
        c = m
    return z, i, n


@njit(cache=True)
def priority_flood(z):
    """
    Fill depressions with a tiny gradient (nextafter) so that flats drain; z <= 0 is no data
    (the sea, or nothing), and it and the box's edge are where water leaves. Returns the filled
    heights, the order the flood reached the cells (downstream before upstream), and per cell
    whether it lies in a filled depression (water would pool there: a sink if nothing else).
    """
    H, W = z.shape
    n = H * W
    f = z.copy()
    hz = np.empty(n, np.float32)
    hi = np.empty(n, np.int64)
    done = np.zeros(n, np.bool_)
    order = np.empty(n, np.int64)
    pooled = np.zeros(n, np.bool_)
    m = 0
    for r in range(H):
        for c in range(W):
            i = r * W + c
            if f[r, c] <= 0:
                done[i] = True
                continue
            edge = r == 0 or c == 0 or r == H - 1 or c == W - 1
            if not edge:
                for k in range(8):
                    if f[r + DY[k], c + DX[k]] <= 0:
                        edge = True
                        break
            if edge:
                m = _push(hz, hi, m, f[r, c], i)
                done[i] = True
    k_out = 0
    while m > 0:
        zc, i, m = _pop(hz, hi, m)
        order[k_out] = i
        k_out += 1
        r, c = i // W, i % W
        for k in range(8):
            rr, cc = r + DY[k], c + DX[k]
            if rr < 0 or cc < 0 or rr >= H or cc >= W:
                continue
            j = rr * W + cc
            if done[j]:
                continue
            done[j] = True
            if f[rr, cc] <= zc:
                f[rr, cc] = np.nextafter(zc, np.float32(np.inf))
                pooled[j] = True
            m = _push(hz, hi, m, f[rr, cc], j)
    return f, order[:k_out], pooled.reshape(H, W)


@njit(cache=True)
def flow(f):
    """Each cell's downstream neighbour (steepest drop, by distance), or -1 where water leaves."""
    H, W = f.shape
    down = np.full(H * W, -1, np.int64)
    for r in range(H):
        for c in range(W):
            if f[r, c] <= 0:
                continue
            best, bs = -1, 0.0
            for k in range(8):
                rr, cc = r + DY[k], c + DX[k]
                if rr < 0 or cc < 0 or rr >= H or cc >= W or f[rr, cc] <= 0:
                    continue
                d = 1.4142135 if DY[k] != 0 and DX[k] != 0 else 1.0
                s = (f[r, c] - f[rr, cc]) / d
                if s > bs:
                    bs, best = s, rr * W + cc
            down[r * W + c] = best
    return down


@njit(cache=True)
def accumulate(order, down, cell_area):
    """Catchment area (km²) of each cell, upstream cells first (the flood order, reversed)."""
    acc = np.zeros(len(down), np.float64)
    for k in range(len(order) - 1, -1, -1):
        i = order[k]
        acc[i] += cell_area[i]
        if down[i] >= 0:
            acc[down[i]] += acc[i]
    return acc


@njit(cache=True)
def basin_high(order, down, z):
    """Each cell's highest upstream cell (its catchment's high point), upstream first."""
    hi = np.arange(len(down))
    zf = z.ravel()
    for k in range(len(order) - 1, -1, -1):
        i = order[k]
        j = down[i]
        if j >= 0 and zf[hi[i]] > zf[hi[j]]:
            hi[j] = hi[i]
    return hi


@njit(cache=True)
def downstream_order(down):
    """Cells ordered downstream before upstream (as the flood's order is), from `down` alone."""
    n = len(down)
    ind = np.zeros(n, np.int64)
    for i in range(n):
        if down[i] >= 0:
            ind[down[i]] += 1
    stack = np.empty(n, np.int64)
    m = 0
    for i in range(n):
        if ind[i] == 0:
            stack[m] = i
            m += 1
    up_first = np.empty(n, np.int64)
    k = 0
    while m > 0:
        m -= 1
        i = stack[m]
        up_first[k] = i
        k += 1
        j = down[i]
        if j >= 0:
            ind[j] -= 1
            if ind[j] == 0:
                stack[m] = j
                m += 1
    return up_first[:k][::-1].copy()


def drain_lakes(f, label, down, nlakes):
    """
    Every cell of a water body drains through one outlet: the edge cell whose outside neighbour
    lies lowest (the dam), along a breadth-first tree through the water. Returns the outlets;
    `label` loses the pieces the outlet cannot reach.
    """
    from collections import deque
    H, W = label.shape
    lab, ff = label.ravel(), f.ravel()
    outlets = {}
    for k in range(1, nlakes + 1):
        cells = np.flatnonzero(lab == k)
        if not len(cells):
            continue
        best = None
        for c in cells:
            r, q = divmod(int(c), W)
            for dy, dx in zip(DY, DX):
                rr, cc = r + dy, q + dx
                if rr < 0 or cc < 0 or rr >= H or cc >= W:
                    cand = (-1e9, int(c), -1)          # the water reaches the edge of the box
                else:
                    j = rr * W + cc
                    if lab[j] == k:
                        continue
                    cand = (float(ff[j]) if ff[j] > 0 else -1e9, int(c), j if ff[j] > 0 else -1)
                if best is None or cand < best:
                    best = cand
        if best is None:
            continue
        _, o, out = best
        down[o] = out
        seen, dq = {o}, deque([o])
        while dq:
            c = dq.popleft()
            r, q = divmod(c, W)
            for dy, dx in zip(DY, DX):
                rr, cc = r + dy, q + dx
                if 0 <= rr < H and 0 <= cc < W:
                    j = rr * W + cc
                    if lab[j] == k and j not in seen:
                        seen.add(j)
                        down[j] = c
                        dq.append(j)
        outlets[k] = o
        # pieces of it the grid cut off from the outlet (a narrow neck under 30 m): land, which the
        # flood has drained already
        rest = [int(c) for c in cells if int(c) not in seen]
        if rest:
            lab[rest] = 0
    return outlets


class Grid:
    """
    A raster window: H × W cells, `transform` from (col, row) of the cell's corner to the CRS,
    `crs` (None: geographic lon/lat). The cell centres are what `latlon` and `cell_of` speak of.
    """
    def __init__(self, H, W, transform, crs=None, sea=True):
        self.H, self.W, self.transform, self.crs = H, W, transform, crs
        self.sea = sea                     # is no data the sea (FABDEM), or only where data ends?
        if crs:
            from pyproj import Transformer
            self.to_ll = Transformer.from_crs(crs, 'EPSG:4326', always_xy=True)
            self.from_ll = Transformer.from_crs('EPSG:4326', crs, always_xy=True)

    @classmethod
    def geographic(cls, s, n, w, e):
        from rasterio.transform import from_origin
        return cls((n - s) * ARC, (e - w) * ARC, from_origin(w - 0.5 / ARC, n + 0.5 / ARC, 1 / ARC, 1 / ARC))

    def latlon(self, i):
        r, c = divmod(int(i), self.W)
        x, y = self.transform * (c + 0.5, r + 0.5)
        if self.crs:
            x, y = self.to_ll.transform(x, y)
        return y, x

    def cell_of(self, lat, lon):
        """ The cell at a point, or None outside the window. """
        x, y = (lon, lat) if not self.crs else self.from_ll.transform(lon, lat)
        c, r = ~self.transform * (x, y)
        r, c = int(math.floor(r)), int(math.floor(c))
        return r * self.W + c if 0 <= r < self.H and 0 <= c < self.W else None

    def project(self, geom):
        """ A lon/lat shapely geometry in the grid's CRS. """
        if not self.crs:
            return geom
        from shapely.ops import transform
        return transform(lambda x, y, z=None: self.from_ll.transform(x, y), geom)

    def cell_area(self):
        """ km² of each cell. """
        if self.crs:
            return np.full(self.H * self.W, abs(self.transform.a * self.transform.e) / 1e6)
        lat = np.array([self.latlon(r * self.W)[0] for r in range(self.H)])
        row = (1 / ARC * 110.574) * (1 / ARC * 111.32 * np.cos(np.radians(lat)))
        return np.repeat(row, self.W)


def dmr5_grid(s, n, w, e, res):
    """
    DMR 5.0 (Slovakia's lidar terrain, 1 m, ETRS89 / UTM 34N, ellipsoidal heights) over the box
    S,N,W,E, averaged to `res` m; no data (beyond the border) as 0. Returns (grid, z).
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import Affine
    from rasterio.windows import Window
    from prominence import SOURCE
    d = rasterio.open(SOURCE)
    from pyproj import Transformer
    t = Transformer.from_crs('EPSG:4326', d.crs, always_xy=True)
    xs, ys = zip(*[t.transform(lo, la) for la in (s, n) for lo in (w, e)])
    r0, c0 = d.index(min(xs), max(ys))
    r1, c1 = d.index(max(xs), min(ys))
    r0, c0 = max(r0, 0), max(c0, 0)
    r1, c1 = min(r1, d.height), min(c1, d.width)
    k = int(res)
    H, W = (r1 - r0) // k, (c1 - c0) // k
    z = d.read(1, window=Window(c0, r0, W * k, H * k), out_shape=(H, W), resampling=Resampling.average,
               masked=True).filled(0).astype(np.float32)
    z[~np.isfinite(z) | (z > 1e30) | (z < -1000)] = 0
    x0, y0 = d.transform * (c0, r0)
    return Grid(H, W, Affine(k, 0, x0, 0, -k, y0), crs=d.crs.to_string(), sea=False), z


def osm_index(path, cell=0.01):
    """Named waterway segments by grid cell: (lat1, lon1, lat2, lon2, name, kind)."""
    segs = {}
    for e in json.load(open(path))['elements']:
        name = e.get('tags', {}).get('name')
        g = e.get('geometry')
        if not name or not g:
            continue
        kind = e['tags'].get('waterway', '')
        if kind not in ('river', 'stream'):
            continue          # a canal runs beside the river it was dug from, under another name
        for a, b in zip(g, g[1:]):
            for key in {(math.floor(a['lat'] / cell), math.floor(a['lon'] / cell)),
                        (math.floor(b['lat'] / cell), math.floor(b['lon'] / cell))}:
                segs.setdefault(key, []).append((a['lat'], a['lon'], b['lat'], b['lon'], name, kind))
    return segs, cell


def seg_dist_m(lat, lon, s):
    """Metres from a point to an OSM segment (local flat approximation)."""
    k = math.cos(math.radians(lat))
    ax, ay = (s[1] - lon) * 111320 * k, (s[0] - lat) * 110574
    bx, by = (s[3] - lon) * 111320 * k, (s[2] - lat) * 110574
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def osm_near(index, lat, lon, within=150.0):
    """The name of the nearest named waterway within `within` m, or None."""
    segs, cell = index
    best = None
    ci, cj = math.floor(lat / cell), math.floor(lon / cell)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for s in segs.get((ci + di, cj + dj), ()):
                d = seg_dist_m(lat, lon, s)
                if d <= within and (best is None or d < best[0]):
                    best = (d, s[4])
    return best[1] if best else None


NOT_LAKES = {'river', 'canal', 'stream', 'ditch', 'drain', 'wastewater', 'lock', 'moat'}


def load_lakes(path, g, min_km2):
    """
    Lakes and reservoirs: OSM water polygons that are not rivers (Overpass `out geom` of
    natural=water and landuse=reservoir), touching pieces merged, at least `min_km2` each.
    Returns the label grid (0: none, k: lakes[k - 1]) and the lakes {name, km2, polygon}.
    """
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import LineString, Polygon, mapping
    from shapely.ops import polygonize, unary_union
    pieces = []
    for e in json.load(open(path))['elements']:
        t = e.get('tags', {})
        if t.get('water') in NOT_LAKES:
            continue
        if e['type'] == 'way':
            q = e.get('geometry') or []
            if len(q) < 4 or q[0] != q[-1]:
                continue
            poly = Polygon([(v['lon'], v['lat']) for v in q])
        else:
            outer = [LineString([(v['lon'], v['lat']) for v in m['geometry']])
                     for m in e.get('members', []) if m.get('role') == 'outer' and m.get('geometry')]
            poly = unary_union(list(polygonize(unary_union(outer)))) if outer else None
        if poly is None or poly.is_empty:
            continue
        pieces.append((poly.buffer(0), t.get('name')))

    def km2(poly):
        return poly.area * 110.574 * 111.32 * math.cos(math.radians(poly.centroid.y))
    merged = unary_union([q for q, _ in pieces])
    lakes = []
    for poly in getattr(merged, 'geoms', [merged]):
        a = km2(poly)
        if a < min_km2:
            continue
        named = [(km2(q), nm) for q, nm in pieces if nm and q.intersects(poly)]
        lakes.append(dict(name=max(named)[1] if named else None, km2=round(a, 2), polygon=poly))
    label = rasterize([(mapping(g.project(lk['polygon'])), k + 1) for k, lk in enumerate(lakes)],
                      out_shape=(g.H, g.W), transform=g.transform, fill=0, dtype='int32') \
        if lakes else np.zeros((g.H, g.W), np.int32)
    return label, lakes


def simplify(points, metres=10.0):
    """ A course as [lat, lon] at 0.00001°, without the points it runs straight through. """
    if len(points) < 3:
        return [[round(y, 5), round(x, 5)] for y, x in points]
    from shapely.geometry import LineString
    k = math.cos(math.radians(points[0][0]))
    ls = LineString([(x * k, y) for y, x in points]).simplify(metres / 110574)
    return [[round(y, 5), round(x / k, 5)] for x, y in ls.coords]


def burn_lines(path, g):
    """
    The cells under OpenStreetMap's named rivers and streams (not canals, which run beside the
    river they were dug from): where to lower the model so that its channels follow the mapped
    streams across fans, floodplains and embankments, which a 30 m model smooths away.
    """
    from rasterio.features import rasterize
    lines = []
    for e in json.load(open(path))['elements']:
        t = e.get('tags', {})
        q = e.get('geometry')
        if t.get('name') and q and len(q) > 1 and t.get('waterway') in ('river', 'stream'):
            lines.append({'type': 'LineString', 'coordinates': [(v['lon'], v['lat']) for v in q]})
    from shapely.geometry import shape, mapping
    return rasterize([(mapping(g.project(shape(ln))), 1) for ln in lines], out_shape=(g.H, g.W),
                     transform=g.transform, fill=0, dtype='uint8').astype(bool) \
        if lines else np.zeros((g.H, g.W), bool)


def norm_name(s):
    """ A river name to compare: no disambiguation in brackets, case folded. """
    import re
    return re.sub(r'\s*\(.*?\)', '', s or '').strip().casefold()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--bbox', required=True, help='S,N,W,E (whole degrees for FABDEM)')
    ap.add_argument('--dmr5', type=float, metavar='RES',
                    help="Slovakia's DMR 5.0 lidar averaged to RES m instead of FABDEM")
    ap.add_argument('--osm', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--min-area', type=float, default=1.0, help='km² draining into a cell to make it channel')
    ap.add_argument('--river-area', type=float, default=5.0, help='km² a river must drain to be kept')
    ap.add_argument('--lakes', help='Overpass `out geom` of natural=water and landuse=reservoir in the box')
    ap.add_argument('--lake-area', type=float, default=0.1, help='km² a water body must cover to count')
    ap.add_argument('--burn', type=float, default=3.0,
                    help="metres to lower the model along OSM's named streams (0: not at all)")
    ap.add_argument('--known', help="TreeLine's rivers: [{pk, name, source: {lat, lon, altitude}, mouth: {lat, lon}}]")
    a = ap.parse_args()
    s, n, w, e = (float(v) for v in a.bbox.split(','))
    t0 = time.time()
    if a.dmr5:
        g, z = dmr5_grid(s, n, w, e, a.dmr5)
        dem, datum = f'DMR 5.0 at {a.dmr5:g} m', 'ETRS89-h'
    else:
        s, n, w, e = (int(v) for v in (s, n, w, e))
        g = Grid.geographic(s, n, w, e)
        z = fabdem_reader(n, w)(0, g.H, 0, g.W)
        dem, datum = 'FABDEM V1-2', 'EGM2008'
    print(f'{g.H} × {g.W} cells read in {time.time() - t0:.0f} s', flush=True)
    label, lakes = load_lakes(a.lakes, g, a.lake_area) if a.lakes else (np.zeros((g.H, g.W), np.int32), [])
    zf = z.copy()
    if a.burn:
        burnt = burn_lines(a.osm, g) & (z > a.burn)
        zf[burnt] -= a.burn
        print(f'{burnt.sum()} cells under named OSM streams lowered by {a.burn:g} m', flush=True)
    for k, lk in enumerate(lakes):
        m = (label == k + 1) & (z > 0)
        lk['level'] = float(np.median(z[m])) if m.any() else None
        if m.any():
            zf[m] = lk['level']     # flat water: the flood drains it through one outlet, the lowest edge
    print(f'{len(lakes)} water bodies of {a.lake_area:g} km² or more', flush=True)
    f, order, pooled = priority_flood(zf)
    down = flow(f)
    lake_outlets = drain_lakes(f, label, down, len(lakes))
    if lakes:
        order = downstream_order(down)
    acc = accumulate(order, down, g.cell_area())
    # distances below are counted in cells of FABDEM's ~30 m; on a finer grid, proportionally more
    sc = max(1, round(30 / (a.dmr5 or 30)))
    high = basin_high(order, down, z)
    print(f'drainage in {time.time() - t0:.0f} s', flush=True)

    lake = label.ravel()
    outlet_of = lake_outlets               # lake number -> its outlet cell, and back
    lake_at = {o: k for k, o in outlet_of.items()}
    channel = (acc >= a.min_area) & (lake == 0)
    for o in lake_at:
        channel[o] = True                  # a water body is one node, at its outlet
    ch_idx = np.flatnonzero(channel)
    # upstream channel children of every channel cell; whatever enters a water body is a child
    # of its outlet
    kids, attached = {}, set()
    for i in ch_idx:
        j = down[i]
        if j < 0:
            continue
        k = lake[j]
        if k and lake[i] != k:
            if k in outlet_of:
                kids.setdefault(outlet_of[k], []).append(int(i))
                attached.add(int(i))
        elif channel[j]:
            kids.setdefault(int(j), []).append(int(i))
            attached.add(int(i))
    outlets = [int(i) for i in ch_idx if int(i) not in attached]

    known = []
    if a.known:
        cols = {}
        for kr in json.load(open(a.known)):
            sp = kr.get('source')
            c = g.cell_of(sp['lat'], sp['lon']) if sp else None
            if c is None or z.flat[c] <= 0:
                continue
            path = [c]
            while down[path[-1]] >= 0 and len(path) < g.H * 4:
                path.append(int(down[path[-1]]))
            known.append(dict(kr, parts=[norm_name(x) for x in (kr['name'] or '').split(' / ') if x.strip()],
                              path=path, at={c: n for n, c in enumerate(path)}))
        print(f'{len(known)} TreeLine rivers with a source in the box', flush=True)
    used = set()

    def known_for(mouth):
        """ The TreeLine river this mouth starts: its source drains through here, and the name or
        its own mouth agrees. """
        cands = [k for k, kr in enumerate(known) if k not in used and mouth in kr['at']]
        if not cands:
            return None
        la, lo = g.latlon(mouth)
        near = lambda k: (km_ll(known[k]['mouth'], la, lo) if known[k].get('mouth') else 99.0)
        # TreeLine's own mouth first (OSM may name the lowest reach after the other branch); then
        # names read up the river, not only at its mouth (where the parent's own line is near)
        close = [k for k in cands if near(k) <= 0.5]
        if close:
            return min(close, key=near)
        read = {norm_name(name_at(ahead(mouth, c))) for c in (4 * sc, 8 * sc, 16 * sc, 32 * sc, 64 * sc)} - {''}
        named = [k for k in cands if read & set(known[k]['parts'])]
        return min(named, key=near) if named else None

    def km_ll(p, la, lo):
        return math.hypot((p['lat'] - la) * 110.574, (p['lon'] - lo) * 111.32 * math.cos(math.radians(la)))
    index = osm_index(a.osm)

    def ends(i):
        r, c = divmod(i, g.W)
        if r in (0, g.H - 1) or c in (0, g.W - 1):
            return 'edge'
        if down[i] < 0:
            ns = [z[r + dy, c + dx] for dy, dx in zip(DY, DX)]
            return 'sea' if g.sea and min(ns) <= 0 else 'edge' if min(ns) <= 0 else 'sink'
        return 'sink'

    def name_at(i):
        la, lo = g.latlon(i)
        return osm_near(index, la, lo)

    def branch_name(i):
        """A branch's name: the most frequent of four readings up it (one may hit a neighbour)."""
        votes = {}
        for cells in (4 * sc, 8 * sc, 12 * sc, 16 * sc):
            nm = name_at(ahead(i, cells))
            if nm:
                votes[nm] = votes.get(nm, 0) + 1
        return max(votes, key=votes.get) if votes else None

    def ahead(i, cells):
        """A cell `cells` up the larger branch from i: where to read a branch's name."""
        for _ in range(cells):
            up = kids.get(i)
            if not up:
                break
            i = max(up, key=lambda j: acc[j])
        return i

    rivers = []
    todo = [(i, None, None) for i in outlets if acc[i] >= a.river_area]   # (mouth cell, parent key, side)
    while todo:
        mouth, parent, side = todo.pop()
        course, current, seen = [mouth], branch_name(mouth), None
        kn = known_for(mouth)
        if kn is not None:
            used.add(kn)
        reached = False
        while True:
            up = kids.get(course[-1], [])
            kr = known[kn] if kn is not None else None
            on_path = [b for b in up if kr and b in kr['at']]
            if kr and not on_path:
                if course[-1] in kr['at']:
                    # TreeLine's source lies here, or up a channel too small to be one: follow its
                    # path there and end. What drains in from above is not this river: every big
                    # enough branch is a river of its own, joining here (a source in a reservoir:
                    # the streams entering it).
                    here = course[-1]
                    ext = list(reversed(kr['path'][:kr['at'][here]]))
                    for t in up:
                        if acc[t] >= a.river_area:
                            if here in lake_at:
                                todo.append((t, len(rivers), ('lake', lake_at[here], [here] + ext, t)))
                            else:
                                todo.append((t, len(rivers), (here, ext[0] if ext else here, t)))
                    course.extend(ext)
                    reached = True
                    break
                kn = None                  # its source drains elsewhere in the model: the rules decide
            if not up:
                break
            # Which branch the river continues up: where TreeLine knows the river, the one its source
            # drains through (custom decides, as hydrology does); otherwise the one carrying its name
            # when the larger branch clearly reads another; otherwise the larger catchment.
            up.sort(key=lambda i: -acc[i])
            main = up[0]
            if on_path:
                main = on_path[0]
            elif len(up) > 1 and current:
                big = branch_name(up[0])
                if big is not None and big != current:
                    same = [b for b in up[1:] if acc[b] >= a.min_area and branch_name(b) == current]
                    if len(same) == 1:
                        main = same[0]
            here = course[-1]
            if here in lake_at:
                # Through the water to the branch chosen: its flow path to the outlet, whatever the
                # other branches do; they are tributaries of this river, in this water body.
                path, c = [], int(down[main])
                while c != here and c >= 0 and len(path) < g.H * 4:
                    path.append(c)
                    c = int(down[c])
                course.extend(reversed(path))
                through = [here] + path[::-1] + [main]
                for t in up:
                    if t != main and acc[t] >= a.river_area:
                        todo.append((t, len(rivers), ('lake', lake_at[here], through, t)))
                course.append(main)
                continue
            course.append(main)
            if len(course) % (10 * sc) == 0:
                # the river's own name changes only when two readings in a row agree
                nm = name_at(course[-1])
                if nm and nm == seen:
                    current = nm
                seen = nm
            for t in up:                  # every other branch: a tributary, if big enough
                if t != main and acc[t] >= a.river_area:
                    todo.append((t, len(rivers), (course[-2], main, t)))
        rivers.append(dict(course=course, parent=parent, junction=side,
                           known=known[kn] if reached else None))

    # names, sources, sides
    out = []
    for key, rv in enumerate(rivers):
        course = rv['course']             # mouth first
        names, read = {}, []
        step = max(1, len(course) // 300)
        # a tributary's first ~180 m lie within reach of its parent's own line: not its name
        skip = 6 * sc if rv['parent'] is not None else 0
        for k in range(min(skip, len(course) - 1), len(course), step):
            la, lo = g.latlon(course[k])
            nm = osm_near(index, la, lo)
            if nm:
                names[nm] = names.get(nm, 0) + step
                read.append((k, nm))
        name, votes = max(names.items(), key=lambda kv: kv[1]) if names else (None, 0)
        cover = votes / len(course)
        if cover < 0.2:
            name = None            # a few readings near another river's line (a gravel pit's outflow)
        # its source: where OSM stops mapping it under its own name, else the channel head
        covered_up = max((k for k, nm in read if nm == name), default=None)
        src_k = covered_up if covered_up is not None else len(course) - 1
        if rv['known']:
            src_k = len(course) - 1        # up to TreeLine's own spring
        src, mth = course[src_k], course[0]
        side, in_lake = None, None
        if rv['junction'] and rv['junction'][0] == 'lake':
            # Facing downstream along the river through the water, which shore does it come from?
            _, k, through, t = rv['junction']
            in_lake = lakes[k - 1]
            tr_, tc_ = divmod(t, g.W)
            near = min(range(len(through)), key=lambda n: (divmod(through[n], g.W)[0] - tr_) ** 2
                       + (divmod(through[n], g.W)[1] - tc_) ** 2)
            (ur, uc), (dr, dc) = divmod(through[min(near + 8 * sc, len(through) - 1)], g.W), divmod(through[max(near - 8 * sc, 0)], g.W)
            nr, nc = divmod(through[near], g.W)
            vx, vy, px, py = dc - uc, ur - dr, tc_ - nc, nr - tr_
            cross = vx * py - vy * px
            side = 'L' if cross > 0 else 'R' if cross < 0 else 'O'
        elif rv['junction']:
            conf, main_up, _ = rv['junction']
            # Facing downstream at the confluence, is the tributary to the left or the right?
            # Both directions over about 300 m (ten cells), so that the eight-direction grid's
            # stair-steps do not decide it: the main river from ten cells above the confluence
            # to ten below, the tributary from ten cells up its own course.
            above, below = ahead(main_up, 10 * sc - 1), conf
            for _ in range(10 * sc):
                if down[below] < 0:
                    break
                below = down[below]
            (orr, oc), (tr_, tc_) = divmod(above, g.W), divmod(below, g.W)
            vx, vy = tc_ - oc, orr - tr_
            cr, cc = divmod(conf, g.W)
            up_r, up_c = divmod(course[min(10 * sc, len(course) - 1)], g.W)
            px, py = up_c - cc, cr - up_r
            cross = vx * py - vy * px
            side = 'L' if cross > 0 else 'R' if cross < 0 else 'O'
        hp = int(high[mth])
        la_s, lo_s = g.latlon(src)
        la_m, lo_m = g.latlon(mth)
        la_h, lo_h = g.latlon(hp)
        length = 0.0
        for p, q in zip(course[:src_k], course[1:src_k + 1]):
            (y1, x1), (y2, x2) = g.latlon(p), g.latlon(q)
            length += math.hypot((y2 - y1) * 110.574, (x2 - x1) * 111.32 * math.cos(math.radians(y1)))
        source = dict(lat=round(la_s, 6), lon=round(lo_s, 6), altitude=round(float(z.flat[src]), 1),
                      datum=datum, from_='osm' if covered_up is not None else 'dem')
        if rv['known']:
            ks = rv['known']['source']
            source = dict(lat=ks['lat'], lon=ks['lon'], altitude=ks['altitude'], datum=None, from_='treeline')
        crossed = []
        for c in course:
            k = lake[c]
            if k and (not crossed or crossed[-1] != k):
                crossed.append(int(k))
        out.append(dict(
            key=f'riv:{la_m:.5f},{lo_m:.5f}', name=name, osm_cover=round(cover, 2),
            treeline=rv['known'] and dict(pk=rv['known'].get('pk'), name=rv['known']['name']),
            source=source,
            mouth=dict(lat=round(la_m, 6), lon=round(lo_m, 6), datum=datum,
                       altitude=round(in_lake['level'] if in_lake else float(z.flat[mth]), 1)),
            lake=in_lake and in_lake['name'], through=[lakes[k - 1]['name'] for k in crossed],
            parent=None if rv['parent'] is None else rv['parent'], mouth_side=side if rv['parent'] is not None else None,
            ends=None if rv['parent'] is not None else ends(mth),
            catchment_km2=round(float(acc[mth]), 2), length_km=round(length, 2),
            watershed_high_point=dict(lat=round(la_h, 6), lon=round(lo_h, 6), altitude=round(float(z.flat[hp]), 1)),
            course=simplify([g.latlon(c) for c in course[:src_k + 1]] if not rv['known'] else
                            [g.latlon(c) for c in course] + [(source['lat'], source['lon'])])))
    keys = [r['key'] for r in out]
    for r in out:
        if r['parent'] is not None:
            r['parent'] = keys[r['parent']]
        r['source']['from'] = r['source'].pop('from_')
    json.dump(dict(sources=dict(dem=dem, names='OpenStreetMap'),
                   settings=dict(bbox=[s, n, w, e], min_area=a.min_area, river_area=a.river_area,
                                 lake_area=a.lake_area),
                   lakes=[dict(name=lk['name'], km2=lk['km2'], level=lk['level'] and round(lk['level'], 1),
                               polygon=[[round(y, 5), round(x, 5)] for x, y in
                                        lk['polygon'].simplify(0.0003).exterior.coords]
                               if lk['polygon'].geom_type == 'Polygon' else None) for lk in lakes],
                   rivers=out), open(a.out, 'w'), ensure_ascii=False, indent=1)
    named = sum(1 for r in out if r['name'])
    print(f'{len(out)} rivers ({named} named) in {time.time() - t0:.0f} s -> {a.out}')


if __name__ == '__main__':
    main()
