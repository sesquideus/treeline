"""
Terrain readers for the isolation search (`isolation.py`) and the climb to the isolation parent
(`nhn.py`): any terrain as one interface, so the search and the climb need not know which data
lies under them.

A reader is a tree of boxes in lon/lat, each with the highest height in it (EGM2008 metres). The
search descends it best-first by distance and opens the data only in its leaves, as a `Patch`:
the leaf's cells with one cell more all round (so that the refinement on the surface between cell
centres sees their neighbours), and a map from pixel coordinates to lon/lat.

- `LidarTerrain`: the int-cm tiles of the sources in `sources.py`, through their tile index
  (`tileindex.py`): index rows, then each row's 32 x 32 coarse maxima, then the cells of one
  coarse cell (~64 m) read at full resolution. Heights become EGM2008 by each source's PROJ
  grid (sources.py `heights`, as `joint.correction` does), on a lattice (`Lattice`); a source PROJ
  cannot convert (the Italian and Slovenian, orthometric) is taken as EGM2008 as it is.
- `ModelTerrain`: a 1" world model in 1 deg tiles (GEDTM30, `catchments.read_model`), with known
  summit heights burned in as `catchments.world --burn` does; tiles, 120" blocks, 12" leaves.
- `HoleTerrain`: Copernicus GLO-30 where no lidar is: what may lie beyond a lidar search's data.

Each also gives a climb frame (`frame()`): a regular grid of the terrain for walking uphill,
the model's own 1" grid, or the lidar pooled to 10 m in EPSG:3035.
"""
import functools
import json
import math
import os
import sqlite3
import sys
import zlib
from collections import OrderedDict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources as _sources  # noqa: E402
import tileindex  # noqa: E402

ARC = 3600
R_EARTH = 6371008.8
CACHE = os.environ.get('TREELINE_ISOLATION_CACHE', os.path.expanduser('~/.cache/treeline/isolation'))


@functools.cache
def geod():
    from pyproj import Geod
    return Geod(ellps='WGS84')


def distances(lon0, lat0, lon, lat):
    """ Geodesic metres from (lon0, lat0) to each of (lon, lat). """
    lon = np.asarray(lon, np.float64)
    lat = np.asarray(lat, np.float64)
    if lon.size == 0:
        return np.zeros(0)
    return np.asarray(geod().inv(np.full(lon.shape, lon0), np.full(lat.shape, lat0), lon, lat)[2])


def haversine(lon0, lat0, lon1, lat1):
    p0, p1 = math.radians(lat0), math.radians(lat1)
    a = math.sin((p1 - p0) / 2) ** 2 + math.cos(p0) * math.cos(p1) * math.sin(math.radians(lon1 - lon0) / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def box_bound(lon, lat, box):
    """ A lower bound of the metres from (lon, lat) to the lon/lat box (lon0, lat0, lon1, lat1). """
    clon = min(max(lon, box[0]), box[2])
    clat = min(max(lat, box[1]), box[3])
    if clon == lon and clat == lat:
        return 0.0
    return max(0.0, haversine(lon, lat, clon, clat) * 0.993 - 1.0)      # sphere vs ellipsoid, corners


class LRU(OrderedDict):
    def __init__(self, size):
        super().__init__()
        self.size = size

    def get_or(self, key, make):
        if key in self:
            self.move_to_end(key)
            return self[key]
        v = self[key] = make()
        if len(self) > self.size:
            self.popitem(last=False)
        return v


class Patch:
    """
    Heights (float64, NaN = no data) of a leaf with one cell more all round, and `lonlat(r, c)`:
    lon/lat of the (fractional) pixel coordinates r, c of this array, where (r, c) integral is a
    cell centre. `burned`: {(r, c): (height, model height, label)} for the cells a burn raised.
    `source`: what the heights are (a label for the isolation's source).
    """

    def __init__(self, z, lonlat, source, burned=None):
        self.z, self.lonlat, self.source, self.burned = z, lonlat, source, burned or {}


# ---------------------------------------------------------------------------------------------- lidar

def egm_transformer(heights):
    from pyproj import Transformer
    return Transformer.from_crs(heights, 'EPSG:4326+3855', always_xy=True, allow_ballpark=False)


def egm_correction(heights, lon, lat):
    """ EGM2008 minus the source's height at (lon, lat) (arrays), 0 where PROJ cannot say (None). """
    lon, lat = np.asarray(lon, np.float64), np.asarray(lat, np.float64)
    if heights is None:
        return np.zeros(lon.shape), False
    _, _, hz = egm_transformer(heights).transform(lon, lat, np.zeros(lon.shape))
    hz = np.asarray(hz, np.float64)
    bad = ~np.isfinite(hz) | (np.abs(hz) > 200)
    hz[bad] = 0.0
    return hz, bool(bad.any())


class Lattice:
    """
    EGM2008 minus a source's height as a function of position alone: PROJ's value at the nodes of a
    STEP-degree lattice, bilinear between them. Every reader of a cell (the search's leaves, a
    summit's top, the climb's frame) then corrects it by the same amount; a correction taken once
    per index row would differ by centimetres between neighbouring rows, enough to make a summit's
    own top look higher than itself a few metres away. Bilinear values never exceed the nodes
    around them, so `bound` (the largest node over a box) is a true upper bound.
    """
    STEP = 0.02

    def __init__(self, heights):
        self.heights = heights
        self.nodes = {}
        self.failed = False

    def _fill(self, keys):
        miss = [k for k in keys if k not in self.nodes]
        if not miss:
            return
        a = np.array(miss, np.float64)
        v, failed = egm_correction(self.heights, a[:, 0] * self.STEP, a[:, 1] * self.STEP)
        self.failed |= failed
        self.nodes.update(zip(miss, v.tolist()))

    def __call__(self, lon, lat):
        lon, lat = np.asarray(lon, np.float64), np.asarray(lat, np.float64)
        if self.heights is None:
            return np.zeros(lon.shape)
        fx, fy = lon / self.STEP, lat / self.STEP
        i, j = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        u, v = fx - i, fy - j
        keys = set()
        for di in (0, 1):
            for dj in (0, 1):
                keys |= set(zip((i + di).ravel().tolist(), (j + dj).ravel().tolist()))
        self._fill(keys)
        g = lambda a, b: np.vectorize(lambda x, y: self.nodes[(x, y)], otypes=[np.float64])(a, b) if a.size else a
        return ((g(i, j) * (1 - u) + g(i + 1, j) * u) * (1 - v) + (g(i, j + 1) * (1 - u) + g(i + 1, j + 1) * u) * v)

    def bound(self, boxes):
        """ The largest correction over each lon/lat box (n x 4): the largest lattice node around it. """
        boxes = np.asarray(boxes, np.float64)
        if self.heights is None:
            return np.zeros(len(boxes))
        i0, j0 = np.floor(boxes[:, 0] / self.STEP).astype(np.int64), np.floor(boxes[:, 1] / self.STEP).astype(np.int64)
        i1, j1 = np.floor(boxes[:, 2] / self.STEP).astype(np.int64) + 1, np.floor(boxes[:, 3] / self.STEP).astype(np.int64) + 1
        keys = set()
        for a, b, c, d in zip(i0, j0, i1, j1):
            keys |= {(x, y) for x in range(a, c + 1) for y in range(b, d + 1)}
        self._fill(keys)
        return np.array([max(self.nodes[(x, y)] for x in range(a, c + 1) for y in range(b, d + 1))
                         for a, b, c, d in zip(i0, j0, i1, j1)])


@functools.cache
def to_lonlat(epsg):
    from pyproj import Transformer
    return Transformer.from_crs(epsg, 4326, always_xy=True)


@functools.cache
def from_lonlat(epsg):
    from pyproj import Transformer
    return Transformer.from_crs(4326, epsg, always_xy=True)


@functools.cache
def between(a, b):
    from pyproj import Transformer
    return Transformer.from_crs(a, b, always_xy=True)


def closing_db(key):
    return sqlite3.connect(tileindex.db_path(key))


def source_name(key):
    """ The isolation's source as the database names it. """
    s = _sources.BY_KEY[key]
    return {'SK': 'DMR 5.0', 'CZ': 'DMR 5G (ČÚZK)', 'AT': 'ALS-DGM (BEV)'}.get(key, f'{s["label"]} ({s["country"]})')


class LidarTerrain:
    """ The int-cm lidar sources through their tile indexes; see the module's docstring. """

    SUPER = (2.0, 0.25)          # degrees: the two levels of boxes above the index rows
    POOL = 5.0                   # m: the frame pools the cells to about this first, then to FRAME_RES
    FRAME_RES = 10.0
    FRAME_CRS = 'EPSG:3035'
    FRAME_Y0 = 6_000_000.0

    def __init__(self, keys=None, box=None, log=print):
        """ `keys`: the sources (default: every source with an index); `box`: lon0, lat0, lon1, lat1. """
        keys = keys or [s['key'] for s in _sources.SOURCES if os.path.exists(tileindex.db_path(s['key']))]
        self.keys, self.src, self.files = [], [], {}
        cols = []
        for key in keys:
            s = _sources.BY_KEY[key]
            con = closing_db(key)
            q = ('SELECT r.id, r.file_id, r.row_off, r.col_off, r.height, r.width, r.zmax, '
                 'l.x0, l.y0, l.x1, l.y1, t.x0, t.y0, t.x1, t.y1 FROM rows r JOIN rows_ll l ON l.id = r.id '
                 'JOIN rows_3035 t ON t.id = r.id WHERE r.zmax IS NOT NULL')
            a = np.array(con.execute(q).fetchall(), np.float64)
            if box is not None and len(a):
                a = a[(a[:, 9] >= box[0]) & (a[:, 7] <= box[2]) & (a[:, 10] >= box[1]) & (a[:, 8] <= box[3])]
            if not len(a):
                continue
            for fid, p, epsg, wkt, tr, scale, nodata, w, h in con.execute(
                    'SELECT id, path, epsg, crs_wkt, transform, scale, nodata, width, height FROM files'):
                # `epsg`: what pyproj takes for the file's CRS, its WKT where it has no EPSG code
                self.files[(key, fid)] = dict(path=os.path.join(_sources.folder(s), p), epsg=epsg or wkt,
                                              transform=json.loads(tr), scale=scale, nodata=nodata, width=w, height=h)
            con.close()
            si = len(self.keys)
            self.keys.append(key)
            lat_ = Lattice(s['heights'])
            corr = lat_.bound(a[:, 7:11])        # the largest correction over each row: upper bounds
            failed = lat_.failed
            if failed:
                log(f'{key}: PROJ could not carry some rows to EGM2008: taken as they are there')
            self.src.append(dict(key=key, res=s['res'], heights=s['heights'], egm=s['heights'] is not None, lattice=lat_,
                                 ids=a[:, 0].astype(np.int64), fid=a[:, 1].astype(np.int64),
                                 win=a[:, 2:6].astype(np.int64), zmax=a[:, 6] + corr, corr=corr,
                                 ll=a[:, 7:11], b3035=a[:, 11:15]))
            cols.append(np.column_stack([np.full(len(a), si), np.arange(len(a))]))
            log(f'{key:10s} {len(a):7d} rows')
        self.rows = np.concatenate(cols) if cols else np.zeros((0, 2), np.int64)
        self.diag = max(s['res'] for s in self.src) * math.sqrt(2) if self.src else 1.5
        self._build_super()
        self._con, self._ds = {}, LRU(64)
        self._coarse = LRU(4096)
        self._pooled = LRU(6000)

    # --- the tree of boxes

    def _build_super(self):
        """ Boxes of SUPER degrees over the rows (by their centres), each the union of its rows. """
        ll = np.concatenate([s['ll'] for s in self.src]) if self.src else np.zeros((0, 4))
        zmax = np.concatenate([s['zmax'] for s in self.src]) if self.src else np.zeros(0)
        cx, cy = (ll[:, 0] + ll[:, 2]) / 2, (ll[:, 1] + ll[:, 3]) / 2
        self.levels = []
        members = np.arange(len(ll))
        for size in self.SUPER[::-1]:                     # fine first, then coarse over the fine
            if not self.levels:
                key = np.floor(cx / size).astype(np.int64) * 100000 + np.floor(cy / size).astype(np.int64)
                groups = {}
                for i, k in enumerate(key):
                    groups.setdefault(int(k), []).append(i)
                boxes = [(np.array(v), ll[v, 0].min(), ll[v, 1].min(), ll[v, 2].max(), ll[v, 3].max(), zmax[v].max())
                         for v in groups.values()]
            else:
                fine = self.levels[-1]
                fx = np.array([(b[1] + b[3]) / 2 for b in fine])
                fy = np.array([(b[2] + b[4]) / 2 for b in fine])
                key = np.floor(fx / size).astype(np.int64) * 100000 + np.floor(fy / size).astype(np.int64)
                groups = {}
                for i, k in enumerate(key):
                    groups.setdefault(int(k), []).append(i)
                boxes = [(np.array(v), min(fine[i][1] for i in v), min(fine[i][2] for i in v),
                          max(fine[i][3] for i in v), max(fine[i][4] for i in v), max(fine[i][5] for i in v))
                         for v in groups.values()]
            self.levels.append(boxes)
        del members

    def roots(self):
        top = len(self.levels) - 1
        return [((b[1], b[2], b[3], b[4]), b[5], ('S', top, i)) for i, b in enumerate(self.levels[top])]

    def children(self, node):
        kind = node[0]
        if kind == 'S':
            _, lvl, i = node
            b = self.levels[lvl][i]
            if lvl == 0:
                out = []
                for r in b[0]:
                    si, ri = self.rows[r]
                    s = self.src[si]
                    out.append((tuple(s['ll'][ri]), s['zmax'][ri], ('R', int(si), int(ri))))
                return out
            return [((c[1], c[2], c[3], c[4]), c[5], ('S', lvl - 1, j)) for j in b[0]
                    for c in [self.levels[lvl - 1][j]]]
        if kind == 'R':
            _, si, ri = node
            zc, boxes = self.coarse(si, ri)
            out = []
            for i in range(zc.shape[0]):
                for j in range(zc.shape[1]):
                    if np.isfinite(zc[i, j]):
                        out.append((tuple(boxes[i, j]), zc[i, j], ('C', si, ri, i, j)))
            return out
        raise ValueError(node)

    def is_leaf(self, node):
        return node[0] == 'C'

    def close(self):
        for c in self._con.values():
            c.close()
        self._con.clear()

    def _db(self, si):
        key = (os.getpid(), si)
        if key not in self._con:
            self._con[key] = sqlite3.connect(tileindex.db_path(self.src[si]['key']))
        return self._con[key]

    def dataset(self, si, fid):
        import rasterio
        f = self.files[(self.src[si]['key'], fid)]
        return self._ds.get_or((os.getpid(), f['path']), lambda: rasterio.open(f['path']))

    def _pix_to_native(self, f, r, c):
        a, b, x0, d, e, y0 = f['transform']
        return x0 + c * a + r * b, y0 + c * d + r * e

    def coarse(self, si, ri):
        """ A row's coarse maxima (EGM2008) and each coarse cell's lon/lat box. """
        def make():
            s = self.src[si]
            row = self._db(si).execute('SELECT coarse_max FROM rows WHERE id = ?', (int(s['ids'][ri]),)).fetchone()
            zc = tileindex.coarse(dict(coarse_max=row[0]), 'max') + s['corr'][ri]
            r0, c0, h, w = s['win'][ri]
            f = self.files[(s['key'], int(s['fid'][ri]))]
            re, ce = tileindex._edges(h), tileindex._edges(w)
            cc, rr = np.meshgrid(c0 + ce, r0 + re)
            x, y = self._pix_to_native(f, rr.astype(np.float64), cc.astype(np.float64))
            lon, lat = to_lonlat(f['epsg']).transform(x, y)
            lon, lat = np.asarray(lon), np.asarray(lat)
            n = tileindex.COARSE
            q = [lon[:-1, :-1], lon[1:, :-1], lon[:-1, 1:], lon[1:, 1:]]
            p = [lat[:-1, :-1], lat[1:, :-1], lat[:-1, 1:], lat[1:, 1:]]
            boxes = np.stack([np.minimum.reduce(q), np.minimum.reduce(p), np.maximum.reduce(q), np.maximum.reduce(p)], -1)
            assert boxes.shape[:2] == (n, n)
            return zc, boxes
        return self._coarse.get_or((si, ri), make)

    def patch(self, node):
        _, si, ri, i, j = node
        s = self.src[si]
        r0, c0, h, w = s['win'][ri]
        re, ce = tileindex._edges(h), tileindex._edges(w)
        fid = int(s['fid'][ri])
        return self.read_patch(si, fid, r0 + re[i] - 1, c0 + ce[j] - 1, re[i + 1] - re[i] + 2, ce[j + 1] - ce[j] + 2)

    def read_patch(self, si, fid, pr0, pc0, ph, pw, corr=None):
        """ Pixels [pr0, pr0 + ph) x [pc0, pc0 + pw) of a file, EGM2008 metres (each cell corrected
        by the source's lattice at its position), NaN outside. """
        from rasterio.windows import Window
        from catchments import heights
        s = self.src[si]
        ds = self.dataset(si, fid)
        f = self.files[(s['key'], fid)]
        v = ds.read(1, window=Window(int(pc0), int(pr0), int(pw), int(ph)), boundless=True, fill_value=ds.nodata)
        z = heights(ds, v).astype(np.float64)

        def lonlat(r, c, f=f, pr0=pr0, pc0=pc0):
            x, y = self._pix_to_native(f, pr0 + np.asarray(r, np.float64) + 0.5, pc0 + np.asarray(c, np.float64) + 0.5)
            lon, lat = to_lonlat(f['epsg']).transform(x, y)
            return np.asarray(lon), np.asarray(lat)
        if s['heights'] is not None:
            # the lattice at a coarse sample of the patch, bilinear between (the correction is smooth)
            n = 9
            rr, cc = np.meshgrid(np.linspace(0, ph - 1, n), np.linspace(0, pw - 1, n), indexing='ij')
            lo, la = lonlat(rr.ravel(), cc.ravel())
            cs = s['lattice'](lo, la).reshape(n, n)
            from scipy.ndimage import map_coordinates
            gr, gc = np.meshgrid(np.arange(ph) * (n - 1) / max(ph - 1, 1), np.arange(pw) * (n - 1) / max(pw - 1, 1),
                                 indexing='ij')
            z = z + map_coordinates(cs, [gr, gc], order=1, mode='nearest')
        return Patch(z, lonlat, s['key'])

    # --- point queries

    def rows_at(self, lon, lat, pad=0.0):
        """ (source, row) of the rows whose lon/lat box holds the point. """
        out = []
        for si, s in enumerate(self.src):
            ll = s['ll']
            m = (ll[:, 0] - pad <= lon) & (ll[:, 2] + pad >= lon) & (ll[:, 1] - pad <= lat) & (ll[:, 3] + pad >= lat)
            out += [(si, int(ri)) for ri in np.flatnonzero(m)]
        return out

    def top(self, lon, lat, radius, ascent=0.0):
        """
        The highest cell within `radius` m of (lon, lat) over every source covering it, and with
        `ascent`, on uphill from there at full resolution to the top it climbs to if that lies
        within `ascent` m (a point a little off its top; a tower stays its own top at 1 m, as it
        would not in a pooled grid): (height EGM2008, lon, lat, source key, None), or None where no
        source has data there.
        """
        from nhn import climb_to
        best = None
        seen = set()
        for si, ri in self.rows_at(lon, lat, pad=0.001):
            s = self.src[si]
            fid = int(s['fid'][ri])
            if (si, fid) in seen:
                continue
            seen.add((si, fid))
            f = self.files[(s['key'], fid)]
            x, y = from_lonlat(f['epsg']).transform(lon, lat)
            a, b, x0, d, e, y0 = f['transform']
            c, r = (x - x0) / a, (y - y0) / e
            if not (0 <= r < f['height'] and 0 <= c < f['width']):
                continue
            k = int(math.ceil((radius + ascent) / s['res'])) + 1
            p = self.read_patch(si, fid, int(r) - k, int(c) - k, 2 * k + 1, 2 * k + 1)
            rr, cc = np.mgrid[0:2 * k + 1, 0:2 * k + 1]
            plon, plat = p.lonlat(rr.ravel(), cc.ravel())
            d = distances(lon, lat, plon, plat).reshape(rr.shape)
            z = np.where((d <= radius) & np.isfinite(p.z), p.z, -np.inf)
            if not np.isfinite(z.max()):
                continue
            i = np.unravel_index(np.argmax(z), z.shape)
            if ascent > 0:
                w = 2 * k + 1
                cells = climb_to(np.ascontiguousarray(p.z, np.float64).ravel(), w, w, int(i[0] * w + i[1]))
                j = divmod(int(cells[0]), w)
                if d[j] <= ascent and p.z[j] > z[i]:
                    i = j
            zi = float(p.z[i])
            if best is None or zi > best[0]:
                best = (zi, float(plon.reshape(rr.shape)[i]), float(plat.reshape(rr.shape)[i]), s['key'], None)
        return best

    # --- the climb frame: EPSG:3035 at 10 m, pooled from every source

    def frame(self):
        return LidarFrame(self)

    def pooled_block(self, si, fid, bi, bj):
        """
        A block of a file pooled to about POOL m (max): its heights and their cells' centres in
        EPSG:3035, cached. Blocks are 100 pooled cells a side.
        """
        def make():
            s = self.src[si]
            f = self.files[(s['key'], fid)]
            k = max(1, int(round(self.POOL / s['res'])))
            B = 100 * k
            pr0, pc0 = bi * B, bj * B
            ph, pw = min(B, f['height'] - pr0), min(B, f['width'] - pc0)
            ph, pw = -(-ph // k) * k, -(-pw // k) * k
            p = self.read_patch(si, fid, pr0, pc0, ph, pw)
            z = p.z.reshape(ph // k, k, pw // k, k)
            with np.errstate(all='ignore'):
                zp = np.fmax.reduce(np.fmax.reduce(z, axis=3), axis=1)
            ok = np.isfinite(zp)
            if not ok.any():
                return None
            rr, cc = np.nonzero(ok)
            x, y = self._pix_to_native(f, pr0 + (rr + 0.5) * k, pc0 + (cc + 0.5) * k)
            X, Y = between(f['epsg'], 3035).transform(x, y)
            return np.asarray(X, np.float64), np.asarray(Y, np.float64), zp[ok].astype(np.float32)
        return self._pooled.get_or((si, fid, bi, bj), make)

    def frame_window(self, gy0, gx0, h, w):
        """ The 10 m EPSG:3035 grid's cells [gy0, gy0 + h) x [gx0, gx0 + w), NaN where no data. """
        R = self.FRAME_RES
        x0, x1 = gx0 * R, (gx0 + w) * R
        y1, y0 = self.FRAME_Y0 - gy0 * R, self.FRAME_Y0 - (gy0 + h) * R
        out = np.full((h, w), np.nan, np.float32)
        for si, s in enumerate(self.src):
            b = s['b3035']
            m = np.flatnonzero((b[:, 2] >= x0) & (b[:, 0] <= x1) & (b[:, 3] >= y0) & (b[:, 1] <= y1))
            for fid in sorted(set(int(v) for v in s['fid'][m])):
                f = self.files[(s['key'], fid)]
                k = max(1, int(round(self.POOL / s['res'])))
                B = 100 * k
                # the window's outline in the file's pixels
                t = np.linspace(0, 1, 9)
                ex = np.concatenate([x0 + (x1 - x0) * t, np.full(9, x1), x1 - (x1 - x0) * t, np.full(9, x0)])
                ey = np.concatenate([np.full(9, y1), y1 - (y1 - y0) * t, np.full(9, y0), y0 + (y1 - y0) * t])
                nx, ny = between(3035, f['epsg']).transform(ex, ey)
                a, bb, fx0, d, e, fy0 = f['transform']
                cs, rs = (np.asarray(nx) - fx0) / a, (np.asarray(ny) - fy0) / e
                r_lo, r_hi = max(0, int(rs.min()) - 1), min(f['height'], int(rs.max()) + 2)
                c_lo, c_hi = max(0, int(cs.min()) - 1), min(f['width'], int(cs.max()) + 2)
                if r_lo >= r_hi or c_lo >= c_hi:
                    continue
                for bi in range(r_lo // B, (r_hi - 1) // B + 1):
                    for bj in range(c_lo // B, (c_hi - 1) // B + 1):
                        blk = self.pooled_block(si, fid, bi, bj)
                        if blk is None:
                            continue
                        X, Y, Z = blk
                        ix = np.floor(X / R).astype(np.int64) - gx0
                        iy = np.floor((self.FRAME_Y0 - Y) / R).astype(np.int64) - gy0
                        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
                        if ok.any():
                            flat = out.ravel()
                            idx = iy[ok] * w + ix[ok]
                            np.fmax.at(flat, idx, Z[ok])
        return out


class LidarFrame:
    """ The climb's grid over the lidar: EPSG:3035, 10 m cells, global indices (gy, gx). """

    def __init__(self, terrain):
        self.t = terrain
        self.cell_m = terrain.FRAME_RES

    def cell_of(self, lon, lat):
        X, Y = from_lonlat(3035).transform(lon, lat)
        return int(math.floor((self.t.FRAME_Y0 - Y) / self.cell_m)), int(math.floor(X / self.cell_m))

    def lonlat(self, gy, gx):
        X = (np.asarray(gx, np.float64) + 0.5) * self.cell_m
        Y = self.t.FRAME_Y0 - (np.asarray(gy, np.float64) + 0.5) * self.cell_m
        lon, lat = to_lonlat(3035).transform(X, Y)
        return np.asarray(lon), np.asarray(lat)

    def window(self, gy0, gx0, h, w):
        return self.t.frame_window(gy0, gx0, h, w)

    def burned(self, gy, gx):
        return None


# ---------------------------------------------------------------------------------------------- 1" model

def tile_name(lat, lon):
    return f'{"N" if lat >= 0 else "S"}{abs(lat):02d}{"E" if lon >= 0 else "W"}{abs(lon):03d}'


def load_burns(paths):
    """
    Burns from `burn_match.py` outputs, as `catchments.load_burns` places them: {(gr, gc) of the
    global 1" grid: (height, label)}, the highest where two fall in one cell.
    """
    out = {}
    for p in paths or []:
        for b in json.load(open(p))['burns']:
            lat, lon = b['at']
            gr, gc = round((90 - lat) * ARC), round((lon + 180) * ARC) % (360 * ARC)
            if (gr, gc) not in out or out[(gr, gc)][0] < float(b['elev']):
                out[(gr, gc)] = (float(b['elev']), b.get('label') or b.get('qid'))
    return out


class GridTerrain:
    """
    A world grid at 1" (cell (gr, gc) centred at lat 90 - gr/3600, lon -180 + gc/3600; the 1 deg
    tile (lat0, lon0) holds rows [(89 - lat0) 3600, +3600)), searched through 12" maxima. A
    subclass says how a tile reads (`read_tile`) and which 12" blocks count (`mask`).
    """

    LEAF = 12                   # cells a side of a leaf
    MID = 10                    # leaves a side of a middle block
    name = 'grid'

    def __init__(self, box, cache=CACHE, tiles=48):
        """ box: lon0, lat0, lon1, lat1 in whole degrees: the data loaded; beyond it is unknown. """
        self.box = box
        self.cache = cache
        self._tiles = LRU(tiles)
        self._summ = {}
        self.diag = math.hypot(31.0, 31.0)

    # tiles
    def tile_path(self, lat, lon):
        raise NotImplementedError

    def read_tile(self, lat, lon):
        raise NotImplementedError

    def summary_key(self, lat, lon):
        p = self.tile_path(lat, lon)
        return f'{self.name}-{tile_name(lat, lon)}-{int(os.stat(p).st_mtime)}' if p and os.path.exists(p) else None

    def tile(self, lat, lon):
        return self._tiles.get_or((os.getpid(), lat, lon), lambda: self.read_tile(lat, lon))

    def summary(self, lat, lon):
        """ The tile's 12" maxima (300 x 300, -inf where nothing counts), cached on disk. """
        if (lat, lon) in self._summ:
            return self._summ[(lat, lon)]
        key = self.summary_key(lat, lon)
        out = None
        if key is not None:
            fn = os.path.join(self.cache, key + '.npy')
            if os.path.exists(fn):
                out = np.load(fn)
            else:
                z = self.tile(lat, lon)
                if z is not None:
                    n = ARC // self.LEAF
                    with np.errstate(all='ignore'):
                        m = np.fmax.reduce(np.fmax.reduce(z.reshape(n, self.LEAF, n, self.LEAF), axis=3), axis=1)
                    out = np.where(np.isfinite(m), m, -np.inf).astype(np.float32)
                    os.makedirs(self.cache, exist_ok=True)
                    tmp = f'{fn}.{os.getpid()}.tmp.npy'
                    np.save(tmp, out)
                    os.replace(tmp, fn)
        if out is not None:
            out = np.where(self.mask(lat, lon), out, -np.inf).astype(np.float32)
        self._summ[(lat, lon)] = out
        return out

    def mask(self, lat, lon):
        return True

    def tiles(self):
        lon0, lat0, lon1, lat1 = self.box
        return [(la, lo) for la in range(lat0, lat1) for lo in range(lon0, lon1)]

    # the tree
    def roots(self):
        out = []
        for la, lo in self.tiles():
            if self.tile_path(la, lo) is None or not os.path.exists(self.tile_path(la, lo)):
                continue
            out.append(((lo, la, lo + 1, la + 1), math.inf, ('T', la, lo)))      # opened when reached
        return out

    def _bbox(self, la, lo, r0, r1, c0, c1):
        """ The lon/lat box of the tile's cells [r0, r1) x [c0, c1), out to the cells' edges. """
        h = 0.5 / ARC
        return (lo + c0 / ARC - h, la + 1 - (r1 - 1) / ARC - h, lo + (c1 - 1) / ARC + h, la + 1 - r0 / ARC + h)

    def children(self, node):
        if node[0] == 'T':
            _, la, lo = node
            s = self.summary(la, lo)
            if s is None:
                return []
            n = s.shape[0] // self.MID
            mid = s.reshape(n, self.MID, n, self.MID).max(axis=(1, 3))
            L = self.LEAF * self.MID
            return [(self._bbox(la, lo, i * L, (i + 1) * L, j * L, (j + 1) * L), float(mid[i, j]), ('M', la, lo, i, j))
                    for i in range(n) for j in range(n) if mid[i, j] > -np.inf]
        if node[0] == 'M':
            _, la, lo, i, j = node
            s = self.summary(la, lo)
            out = []
            for a in range(i * self.MID, (i + 1) * self.MID):
                for b in range(j * self.MID, (j + 1) * self.MID):
                    if s[a, b] > -np.inf:
                        L = self.LEAF
                        out.append((self._bbox(la, lo, a * L, (a + 1) * L, b * L, (b + 1) * L), float(s[a, b]),
                                    ('L', la, lo, a, b)))
            return out
        raise ValueError(node)

    def is_leaf(self, node):
        return node[0] == 'L'

    # cells
    def window(self, gr0, gc0, h, w):
        """ Global 1" cells [gr0, gr0 + h) x [gc0, gc0 + w) from the tiles, NaN where none. """
        out = np.full((h, w), np.nan, np.float32)
        for ty in range(gr0 // ARC, (gr0 + h - 1) // ARC + 1):
            for tx in range(gc0 // ARC, (gc0 + w - 1) // ARC + 1):
                la, lo = 89 - ty, tx - 180
                if not (self.box[1] <= la < self.box[3] and self.box[0] <= lo < self.box[2]):
                    continue
                t = self.tile(la, lo)
                if t is None:
                    continue
                r0, r1 = max(gr0, ty * ARC), min(gr0 + h, (ty + 1) * ARC)
                c0, c1 = max(gc0, tx * ARC), min(gc0 + w, (tx + 1) * ARC)
                out[r0 - gr0:r1 - gr0, c0 - gc0:c1 - gc0] = t[r0 - ty * ARC:r1 - ty * ARC, c0 - tx * ARC:c1 - tx * ARC]
        return out

    def global_cell(self, lon, lat):
        return int(round((90 - lat) * ARC)), int(round((lon + 180) * ARC))

    def patch_at(self, gr0, gc0, h, w):
        z = self.window(gr0, gc0, h, w).astype(np.float64)

        def lonlat(r, c):
            return -180 + (gc0 + np.asarray(r, np.float64) * 0 + np.asarray(c, np.float64)) / ARC, \
                90 - (gr0 + np.asarray(r, np.float64)) / ARC
        return Patch(z, lonlat, self.name, self.burned_in(gr0, gc0, h, w))

    def burned_in(self, gr0, gc0, h, w):
        return {}

    def patch(self, node):
        _, la, lo, a, b = node
        gr0 = (89 - la) * ARC + a * self.LEAF - 1
        gc0 = (lo + 180) * ARC + b * self.LEAF - 1
        return self.patch_at(gr0, gc0, self.LEAF + 2, self.LEAF + 2)

    def top(self, lon, lat, radius):
        gr, gc = self.global_cell(lon, lat)
        k = int(math.ceil(radius / 20.0)) + 1
        p = self.patch_at(gr - k, gc - k, 2 * k + 1, 2 * k + 1)
        rr, cc = np.mgrid[0:2 * k + 1, 0:2 * k + 1]
        plon, plat = p.lonlat(rr.ravel(), cc.ravel())
        d = distances(lon, lat, plon, plat).reshape(rr.shape)
        z = np.where((d <= radius) & np.isfinite(p.z), p.z, -np.inf)
        if not np.isfinite(z.max()):
            return None
        i = np.unravel_index(np.argmax(z), z.shape)
        return (float(z[i]), float(plon.reshape(rr.shape)[i]), float(plat.reshape(rr.shape)[i]), self.name,
                p.burned.get(i))

    def frame(self):
        return GridFrame(self)


class GridFrame:
    """ The climb's grid over a 1" model: its own cells, global indices (gr, gc). """

    def __init__(self, terrain):
        self.t = terrain
        self.cell_m = 25.0

    def cell_of(self, lon, lat):
        return self.t.global_cell(lon, lat)

    def lonlat(self, gy, gx):
        return -180 + np.asarray(gx, np.float64) / ARC, 90 - np.asarray(gy, np.float64) / ARC

    def window(self, gy0, gx0, h, w):
        return self.t.window(gy0, gx0, h, w)

    def burned(self, gy, gx):
        return self.t.burns_model.get((int(gy), int(gx))) if hasattr(self.t, 'burns_model') else None


class ModelTerrain(GridTerrain):
    """
    GEDTM30 (or another 1" model laid out as FABDEM's, `catchments.model_tile`) with burns:
    each burned cell raised to its surveyed height where the model reads lower, as
    `catchments.world_tile` does. A tile missing where Copernicus has land is a hole: what lies
    there is unknown (`holes`), as is everything outside `box`.
    """

    def __init__(self, box, burns=None, cache=CACHE, tiles=40):
        super().__init__(box, cache, tiles)
        import catchments
        self.catchments = catchments
        self.name = catchments.model_name() if catchments.MODEL != 'fabdem' else 'FABDEM V1-2'
        self.burns = load_burns(burns)
        self.burns_model = {}       # (gr, gc): (height, model height, label), for the tiles read so far
        tag = ''
        if self.burns:
            import hashlib
            tag = '-' + hashlib.blake2b(json.dumps(sorted(self.burns.items())).encode(), digest_size=6).hexdigest()
        self.tag = tag
        self.by_tile = {}
        for (gr, gc), v in self.burns.items():
            self.by_tile.setdefault((89 - gr // ARC, gc // ARC - 180), []).append((gr % ARC, gc % ARC, v))
        self._holes = self.holes()

    def summary_key(self, lat, lon):
        k = super().summary_key(lat, lon)
        return k and k + self.tag

    def tile_path(self, lat, lon):
        return self.catchments.model_tile(lat, lon)

    def read_tile(self, lat, lon):
        p = self.tile_path(lat, lon)
        if not os.path.exists(p):
            return None
        z = self.catchments.read_model(p)
        for r, c, (h, label) in self.by_tile.get((lat, lon), ()):
            gr, gc = (89 - lat) * ARC + r, (lon + 180) * ARC + c
            if np.isfinite(z[r, c]):
                if h > z[r, c]:
                    self.burns_model[(gr, gc)] = (h, float(z[r, c]), label)
                    z[r, c] = h
        return z

    def burned_in(self, gr0, gc0, h, w):
        out = {}
        for (gr, gc), v in self.burns_model.items():
            if gr0 <= gr < gr0 + h and gc0 <= gc < gc0 + w:
                out[(gr - gr0, gc - gc0)] = v
        return out

    def holes(self):
        """
        lon/lat boxes of the tiles where Copernicus has land and the model has nothing: no tile, or
        a tile without a single height (a fetch that wrote only no data, as GEDTM30's N48E031).
        Reads every tile's 12" maxima once (cached).
        """
        import catchments
        out = []
        self.empty = []
        for la, lo in self.tiles():
            if not os.path.exists(catchments._glo_name(la, lo, 'DEM')):
                continue
            if not os.path.exists(self.tile_path(la, lo)):
                out.append((lo, la, lo + 1, la + 1))
                continue
            s = self.summary(la, lo)
            if s is None or not np.isfinite(s).any():
                self.empty.append(tile_name(la, lo))
                out.append((lo, la, lo + 1, la + 1))
        return out

    def edge_distance(self, lon, lat):
        """ Metres to the nearest ground whose height is unknown: a hole, or beyond the box. """
        lon0, lat0, lon1, lat1 = self.box
        k = math.cos(math.radians(lat))
        d = min((lat - lat0) * 110574, (lat1 - lat) * 110574, (lon - lon0) * 111320 * k, (lon1 - lon) * 111320 * k)
        for b in getattr(self, '_holes', None) or []:
            d = min(d, box_bound(lon, lat, b))
        return max(d, 0.0)


class HoleTerrain(GridTerrain):
    """
    Copernicus GLO-30 (a surface model: forest canopy included, sharp tops shaved) over the 12"
    blocks no lidar covers (`coverage`), for the question whether the nearest higher ground
    could lie where no lidar is.
    """
    name = 'Copernicus GLO-30'

    def __init__(self, box, coverage, cache=CACHE, tiles=16):
        super().__init__(box, cache, tiles)
        self.coverage = coverage        # Coverage

    def tile_path(self, lat, lon):
        import catchments
        return catchments._glo_name(lat, lon, 'DEM')

    def read_tile(self, lat, lon):
        import rasterio
        import catchments
        p = self.tile_path(lat, lon)
        if not os.path.exists(p):
            return None
        with rasterio.open(p) as d:
            z = catchments._to_arc(d.read(1)).astype(np.float32)
        z[~np.isfinite(z) | (z < -1000)] = np.nan
        # water (the sea, lakes, rivers: Copernicus's water-body mask) hides no higher ground, and
        # lidar often leaves it without data: a fishpond is no hole
        try:
            with rasterio.open(catchments._glo_name(lat, lon, 'WBM')) as d:
                z[catchments._to_arc(d.read(1)) > 0] = np.nan
        except rasterio.errors.RasterioIOError:
            pass
        return z

    def summary_key(self, lat, lon):
        k = super().summary_key(lat, lon)
        return k and k + '-dry'

    def mask(self, lat, lon):
        return ~self.coverage.tile(lat, lon)

    def roots(self):
        out = []
        lon0, lat0, lon1, lat1 = self.box
        for la, lo in self.tiles():
            if not os.path.exists(self.tile_path(la, lo)):
                continue
            if self.coverage.tile(la, lo).all():
                continue
            out.append(((lo, la, lo + 1, la + 1), math.inf, ('T', la, lo)))
        return out

    def edge_distance(self, lon, lat):
        lon0, lat0, lon1, lat1 = self.box
        k = math.cos(math.radians(lat))
        return max(0.0, min((lat - lat0) * 110574, (lat1 - lat) * 110574, (lon - lon0) * 111320 * k,
                            (lon1 - lon) * 111320 * k))


class Coverage:
    """
    Which 12" blocks the lidar covers: the area of the coarse cells with data (from the index's
    `coarse_n`, cells x resolution^2) against the block's own; covered from FULL of it. Built
    once from the indexes and cached.
    """
    FULL = 0.8
    SPREAD = 40.0
    VERSION = 2
    N = ARC // GridTerrain.LEAF

    def __init__(self, terrain, box, cache=CACHE, log=print):
        self.box = box
        lon0, lat0, lon1, lat1 = box
        H, W = (lat1 - lat0) * self.N, (lon1 - lon0) * self.N
        key = '-'.join(f'{k}{len(s["ids"])}' for k, s in zip(terrain.keys, terrain.src))
        import hashlib
        fn = os.path.join(cache, f'coverage{self.VERSION}-{lon0}_{lat0}_{lon1}_{lat1}-'
                                 f'{hashlib.blake2b(key.encode(), digest_size=6).hexdigest()}.npy')
        if os.path.exists(fn):
            self.covered = np.load(fn)
            return
        area = np.zeros(H * W, np.float64)
        n = tileindex.COARSE
        for si, s in enumerate(terrain.src):
            con = sqlite3.connect(tileindex.db_path(s['key']))
            blobs = dict(con.execute('SELECT id, coarse_n FROM rows WHERE zmax IS NOT NULL'))
            con.close()
            idx, wts = [], []

            def flush():
                nonlocal area
                if idx:
                    area += np.bincount(np.concatenate(idx), weights=np.concatenate(wts), minlength=H * W)
                    idx.clear(), wts.clear()
            for k in range(len(s['ids'])):
                if len(idx) >= 4000:
                    flush()
                f = terrain.files[(s['key'], int(s['fid'][k]))]
                r0, c0, h, w = s['win'][k]
                # each coarse cell's area spread over points SPREAD m apart, so that a coarse cell
                # larger than a block (IT-BZ's are 156 m) does not fall into one block whole
                m = max(1, int(math.ceil(max(h, w) / n * s['res'] / self.SPREAD)))
                fr = (np.arange(n * m) + 0.5) / (n * m)
                rr, cc = np.meshgrid(r0 + fr * h, c0 + fr * w, indexing='ij')
                x, y = terrain._pix_to_native(f, rr.ravel(), cc.ravel())
                cnt = np.frombuffer(zlib.decompress(blobs[int(s['ids'][k])]), np.uint16).reshape(n, n).astype(np.float64)
                cnt = np.repeat(np.repeat(cnt, m, axis=0), m, axis=1).ravel() / (m * m)
                lo, la = to_lonlat(f['epsg']).transform(x, y)
                lo, la = np.asarray(lo), np.asarray(la)
                gy = np.floor((lat1 - la) * self.N).astype(np.int64)
                gx = np.floor((lo - lon0) * self.N).astype(np.int64)
                ok = (gy >= 0) & (gy < H) & (gx >= 0) & (gx < W) & (cnt > 0)
                idx.append(gy[ok] * W + gx[ok])
                wts.append(cnt[ok] * s['res'] ** 2)
            flush()
            log(f'coverage: {s["key"]}')
        lat_c = lat1 - (np.arange(H) + 0.5) / self.N
        block = (111320 * np.cos(np.radians(lat_c)) / self.N) * (110574 / self.N)
        self.covered = (area.reshape(H, W) >= self.FULL * block[:, None])
        os.makedirs(cache, exist_ok=True)
        np.save(fn, self.covered)

    def tile(self, lat, lon):
        lon0, lat0, lon1, lat1 = self.box
        if not (lat0 <= lat < lat1 and lon0 <= lon < lon1):
            return np.zeros((self.N, self.N), bool)
        r = (lat1 - lat - 1) * self.N
        c = (lon - lon0) * self.N
        return self.covered[r:r + self.N, c:c + self.N]


# ---------------------------------------------------------------------------------------------- in memory

class ArrayTerrain:
    """
    A grid in memory, cell (r, c) centred at (west + c dlon, north - r dlat): for tests and small
    jobs. Blocks of `block` cells are its leaves under one root per BIG x BIG of them.
    """
    BIG = 8

    def __init__(self, z, west, north, dlon, dlat, block=16, name='array'):
        self.z = np.asarray(z, np.float64)
        self.west, self.north, self.dlon, self.dlat, self.block, self.name = west, north, dlon, dlat, block, name
        H, W = self.z.shape
        self.diag = math.hypot(dlon * 111320 * math.cos(math.radians(north)), dlat * 110574) * 1.01
        self.nb = (-(-H // block), -(-W // block))
        self.bmax = np.full(self.nb, -np.inf)
        for i in range(self.nb[0]):
            for j in range(self.nb[1]):
                v = self.z[i * block:(i + 1) * block, j * block:(j + 1) * block]
                if np.isfinite(v).any():
                    self.bmax[i, j] = np.nanmax(v)

    def _box(self, r0, r1, c0, c1):
        return (self.west + (c0 - 0.5) * self.dlon, self.north - (r1 - 0.5) * self.dlat,
                self.west + (c1 - 0.5) * self.dlon, self.north - (r0 - 0.5) * self.dlat)

    def roots(self):
        B, b = self.BIG, self.block
        out = []
        for I in range(0, self.nb[0], B):
            for J in range(0, self.nb[1], B):
                m = self.bmax[I:I + B, J:J + B].max()
                out.append((self._box(I * b, (I + B) * b, J * b, (J + B) * b), m, ('G', I, J)))
        return out

    def children(self, node):
        _, I, J = node
        b = self.block
        return [(self._box(i * b, (i + 1) * b, j * b, (j + 1) * b), self.bmax[i, j], ('L', i, j))
                for i in range(I, min(I + self.BIG, self.nb[0])) for j in range(J, min(J + self.BIG, self.nb[1]))]

    def is_leaf(self, node):
        return node[0] == 'L'

    def window(self, r0, c0, h, w):
        out = np.full((h, w), np.nan)
        H, W = self.z.shape
        a0, a1, b0, b1 = max(r0, 0), min(r0 + h, H), max(c0, 0), min(c0 + w, W)
        if a0 < a1 and b0 < b1:
            out[a0 - r0:a1 - r0, b0 - c0:b1 - c0] = self.z[a0:a1, b0:b1]
        return out

    def patch(self, node):
        _, i, j = node
        b = self.block
        r0, c0 = i * b - 1, j * b - 1

        def lonlat(r, c):
            r, c = np.asarray(r, np.float64), np.asarray(c, np.float64)
            return self.west + (c0 + c) * self.dlon + 0 * r, self.north - (r0 + r) * self.dlat + 0 * c
        return Patch(self.window(r0, c0, b + 2, b + 2), lonlat, self.name)

    def top(self, lon, lat, radius):
        r, c = int(round((self.north - lat) / self.dlat)), int(round((lon - self.west) / self.dlon))
        k = int(math.ceil(radius / (self.dlat * 110574))) + 1
        z = self.window(r - k, c - k, 2 * k + 1, 2 * k + 1)
        rr, cc = np.mgrid[r - k:r + k + 1, c - k:c + k + 1]
        d = distances(lon, lat, self.west + cc.ravel() * self.dlon, self.north - rr.ravel() * self.dlat).reshape(z.shape)
        z = np.where((d <= radius) & np.isfinite(z), z, -np.inf)
        if not np.isfinite(z.max()):
            return None
        i = np.unravel_index(np.argmax(z), z.shape)
        return float(z[i]), self.west + cc[i] * self.dlon, self.north - rr[i] * self.dlat, self.name, None

    def frame(self):
        return ArrayFrame(self)


class ArrayFrame:
    """ The climb's grid over an ArrayTerrain: its own cells. """

    def __init__(self, terrain):
        self.t = terrain
        self.cell_m = terrain.dlat * 110574

    def cell_of(self, lon, lat):
        return int(round((self.t.north - lat) / self.t.dlat)), int(round((lon - self.t.west) / self.t.dlon))

    def lonlat(self, gy, gx):
        return self.t.west + np.asarray(gx, np.float64) * self.t.dlon, self.t.north - np.asarray(gy, np.float64) * self.t.dlat

    def window(self, gy0, gx0, h, w):
        return self.t.window(gy0, gx0, h, w)

    def burned(self, gy, gx):
        return None
