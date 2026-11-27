"""
Slovakia, Czechia, Poland and Austria as one terrain for the catchment engine: each country's lidar
on one grid (ETRS89-LAEA, EPSG:3035), in one height system (EGM2008), the 1″ model (GEDTM30; FABDEM
with TREELINE_MODEL=fabdem) wherever none of them has data (beyond the four, and Polish sheets not yet
downloaded). The model keeps the key `FABDEM` in --sources.

    cd lidar && uv run --project .. --group lidar python joint.py S,N,W,E OUT.json [--res 1] [--threshold 30]
        [--jobs 12] [--tile 5000] [--line-cutoff 100] [--sources SK,CZ,PLE,PL,AT,FABDEM] [--grid DIR]

`--grid DIR` reads the terrain `joint_grid.py` built once (with its sources) instead of deriving
it from the sources in every tile: faster, and every cell has one value whichever tile reads it.

Every run's tiles sit in one frame over Europe, and every tile and merged block is kept in
`--store` (default $TREELINE_DATA/store/joint) under a hash of what decides it: the files
it reads, the code, the threshold. A run with more data — another country, a bigger box —
floods only the tiles the new files reach and merges only the blocks above them; the rest is
read back. A run cut off resumes the same way. `catchments.py store-gc` deletes blocks no
recorded run uses.

Each top says whether the data settles it: `border` true if its ground reached the edge of the
data before its key col (beyond the edge a higher col may turn up), and `parent_proven` if its
parent cannot change either, whatever data is added (`catchments.walk_settled`).

`--sources` reads only those (all by default). Without FABDEM, everything the others lack is no
data, and a top whose ground reaches it stays open (`border`), as at the window's edge.

Sources, the first with data wins a cell:
  Slovakia  DMR 5.0, 1 m, ETRS89 / UTM 34N, ellipsoidal heights (ETRS89-h); spikes filtered
  Czechia   DMR 5G, triangulated to 1 m by `dmr5g_raster.py`, ETRS89 / TM33N, Bpv
  Poland    GUGiK NMT in PL-EVRF2007-NH, the surveys of 2018 on, 1 m 1:5000 sheets (EPSG:2180)
            from `NMT_PL_EVRF/download.py` (int cm in intcm/PL/EVRF); where none, the older coverage in PL-KRON86-NH,
            1 m in 10 km tiles (EPSG:2180)
  Austria   BEV ALS-DGM, 1 m, 50 km tiles in EPSG:3035 (edition 2025-09-15, CC BY 4.0), EVRF2000
            Austria heights (EPSG:9274); from `ALS_AT/download.sh`
  GEDTM30   1″ (about 30 m), EGM2008, bilinear: what a top resting on it reports is flagged

The lidar is resampled by its nearest cell, so a summit keeps its height. Each country's heights are
carried to EGM2008 through its PROJ geoid grid (the ones CLAUDE.md lists), the correction sampled
every 500 m and interpolated in between: it changes by millimetres over a kilometre. The output is
as `catchments.py raster` writes it, positions in lon/lat, heights EGM2008, each top saying which
source its top cell came from.
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import path  # noqa: E402
from catchments import (ARC, MODEL, THRESHOLD, despike, heights, model_name, model_tile, run_store,  # noqa: E402
                        walk, walk_line, walk_settled)

CRS = 'EPSG:3035'
SK = path('intcm', 'SK', 'dmr5_etrs89.tif', env='TREELINE_DMR5')     # compact.py copies: int cm
CZ = path('intcm', 'CZ', env='TREELINE_DMR5G')
PL = path('intcm', 'PL', 'KRON86', 'tiles', env='TREELINE_NMT_PL')
PLE = path('intcm', 'PL', 'EVRF', env='TREELINE_NMT_PL_EVRF')
AT = path('intcm', 'AT', env='TREELINE_ALS_AT')
#: each source's height system, as PROJ knows it (heights relative to these become EGM2008)
HEIGHTS = {'SK': 'EPSG:4937', 'CZ': 'EPSG:4258+8357', 'PL': 'EPSG:4258+9650',   # PL: Baltic 1986 (KRON86)
           'PLE': 'EPSG:4258+9651',                                               # PLE: EVRF2007-PL
           'AT': 'EPSG:4258+9274'}                                                # AT: EVRF2000 Austria
#: code 4 is the 1″ model beyond the lidar (its key stays 'FABDEM' in --sources and ORDER, whichever model it is)
MODEL_LABEL = 'GEDTM30' if MODEL == 'gedtm30' else 'FABDEM'
NAMES = {0: 'no data', 1: 'DMR 5.0 (SK)', 2: 'DMR 5G (CZ)', 3: 'NMT KRON86 (PL)', 4: MODEL_LABEL, 5: 'NMT EVRF2007 (PL)',
         6: 'ALS-DGM (AT)'}
AT_TILE = 50000
ALL_SOURCES = ('SK', 'CZ', 'PLE', 'PL', 'AT', 'FABDEM')
SOURCES = set(ALL_SOURCES)      # what the reader uses (set by main before the workers fork)
#: islands of data smaller than this (cells) are dropped: slivers at the edge of a source's data,
#: which without FABDEM around them would each be a top of its own. A window is read this many
#: cells wider all round to judge them, so every window decides each island alike.
ISLAND = 200


def model_root():
    """ The folder of the 1″ model's tiles the reader opens beyond the lidar. """
    if MODEL == 'gedtm30':
        from gedtm30 import DIR
        return DIR
    from catchments import FABDEM
    return FABDEM


def at_name(e, n):
    """ The Austrian tile whose south-west corner is (e, n), in metres of EPSG:3035. """
    return f'ALS_DTM_CRS3035RES{AT_TILE}mN{n}E{e}.tif'


def evrf_sheets():
    """ The EVRF2007 sheets downloaded so far: [(easting0, northing0, easting1, northing1, path)],
    bucketed by 10 km cell for lookup. """
    idx = os.path.join(PLE, 'index.json')
    cells = {}
    if not os.path.exists(idx):
        return cells
    for name, s in json.load(open(idx)).items():
        path = os.path.join(PLE, 'sheets', name + '.tif')
        if not os.path.exists(path):
            continue
        e0, n0, e1, n1 = s['bounds']
        # where sheets overlap (those named in the PL-2000 system lie over the EPSG:2180 ones), the
        # newest survey wins, and in the same year the EPSG:2180 sheet: the reader takes the first
        rank = (-s['year'], name[0].isdigit())
        for ek in range(int(e0 // 10000), int(e1 // 10000) + 1):
            for nk in range(int(n0 // 10000), int(n1 // 10000) + 1):
                cells.setdefault((ek, nk), []).append((rank, (e0, n0, e1, n1, path)))
    return {k: [x for _, x in sorted(v)] for k, v in cells.items()}
_STATE = {}


def _sk_bounds():
    """ The Slovak raster's extent in EPSG:3035. """
    import rasterio
    from rasterio.warp import transform_bounds
    with rasterio.open(SK) as d:
        return transform_bounds(d.crs, CRS, *d.bounds, densify_pts=101)


def _state():
    """ Per process: the open files and transformers (forked workers open their own). """
    pid = os.getpid()
    if pid not in _STATE:
        import rasterio
        from pyproj import Transformer
        _STATE.clear()
        _STATE[pid] = dict(
            sk=rasterio.open(SK),
            sk_bounds=_sk_bounds(),
            to_ll=Transformer.from_crs(CRS, 'EPSG:4326', always_xy=True),
            to_cz=Transformer.from_crs(CRS, 'EPSG:3045', always_xy=True),
            to_pl=Transformer.from_crs(CRS, 'EPSG:2180', always_xy=True),
            egm={k: Transformer.from_crs(v, 'EPSG:4326+3855', always_xy=True, allow_ballpark=False)
                 for k, v in HEIGHTS.items()},
            evrf=evrf_sheets())
    return _STATE[pid]


def correction(st, source, x0, y0, res, h, w, step=500.0):
    """
    EGM2008 minus `source`'s height, over a window of the target grid (corner x0, y0 top-left,
    `res` m cells, h × w): sampled every `step` m and interpolated. NaN where PROJ has no grid.
    """
    ny, nx = max(2, int(math.ceil(h * res / step)) + 1), max(2, int(math.ceil(w * res / step)) + 1)
    ys = y0 - np.linspace(0, h * res, ny)
    xs = x0 + np.linspace(0, w * res, nx)
    gx, gy = np.meshgrid(xs, ys)
    lon, lat = st['to_ll'].transform(gx, gy)
    t = st['egm'][source]
    _, _, hz = t.transform(lon.ravel(), lat.ravel(), np.zeros(lon.size))
    hz = np.asarray(hz, dtype=np.float64).reshape(ny, nx)
    hz[~np.isfinite(hz) | (np.abs(hz) > 200)] = np.nan
    # bilinear onto the cell centres
    fy = (np.arange(h) + 0.5) / h * (ny - 1)
    fx = (np.arange(w) + 0.5) / w * (nx - 1)
    iy, ix = np.minimum(fy.astype(int), ny - 2), np.minimum(fx.astype(int), nx - 2)
    wy, wx = (fy - iy)[:, None], (fx - ix)[None, :]
    a, b = hz[iy][:, ix], hz[iy][:, ix + 1]
    c, d = hz[iy + 1][:, ix], hz[iy + 1][:, ix + 1]
    return ((a * (1 - wx) + b * wx) * (1 - wy) + (c * (1 - wx) + d * wx) * wy).astype(np.float32)


def _warp(src_ds, dst, dst_transform, resampling, nodata=None):
    """ `src_ds` resampled onto the window `dst` (shape) of the target grid, in metres; NaN where it
    has nothing. `nodata` is the float originals' no-data value where their file does not declare
    it; an integer copy (`compact.py`) declares its own and carries its unit as the GDAL scale. """
    import rasterio
    from rasterio.warp import reproject
    scaled = np.issubdtype(np.dtype(src_ds.dtypes[0]), np.integer)
    if scaled:
        nodata = src_ds.nodata
    tmp = np.full(dst.shape, np.nan, np.float32)
    reproject(source=rasterio.band(src_ds, 1), destination=tmp, src_transform=src_ds.transform,
              src_crs=src_ds.crs, src_nodata=nodata if nodata is not None else src_ds.nodata,
              dst_transform=dst_transform, dst_crs=CRS, dst_nodata=np.nan, resampling=resampling)
    if scaled:
        tmp = tmp * np.float32(src_ds.scales[0]) + np.float32(src_ds.offsets[0])
    return tmp


def drop_islands(z, size=None):
    """ Set to NaN, in place, every island of data (8-connected) of fewer than `size` cells that
    does not touch the window's edge. """
    from scipy import ndimage
    size = ISLAND if size is None else size
    lab, n = ndimage.label(np.isfinite(z), structure=np.ones((3, 3), bool))
    if not n:
        return 0
    count = np.bincount(lab.ravel(), minlength=n + 1)
    edge = np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))
    small = count < size
    small[0] = False
    small[edge] = False
    z[small[lab]] = np.nan
    return int(small.sum())


#: the reader's order: the first source with data wins a cell (code, name, resampling)
ORDER = ((1, 'SK', 'nearest'), (2, 'CZ', 'nearest'), (5, 'PLE', 'nearest'), (3, 'PL', 'nearest'),
         (6, 'AT', 'nearest'), (4, 'FABDEM', 'bilinear'))


def files(st, source, bounds):
    """
    The files of `source` the reader opens for a window of the target grid with `bounds`
    (x0, y0, x1, y1 in EPSG:3035), each with the no-data value to assume where the file declares
    none: [(path, nodata)]. A window none of them touches gets nothing from that source, which
    is what lets `joint_grid.py extend` keep a block as it was.
    """
    xs, ys = [bounds[0], bounds[2], bounds[0], bounds[2]], [bounds[1], bounds[1], bounds[3], bounds[3]]
    out = []
    if source == 'SK':
        b = st['sk_bounds']
        if b[0] < bounds[2] and bounds[0] < b[2] and b[1] < bounds[3] and bounds[1] < b[3]:
            out.append((SK, None))
    elif source == 'CZ' and os.path.isdir(CZ):
        # 2 km tiles named by their south-west corner in km (EPSG:3045)
        ex, ny = st['to_cz'].transform(xs, ys)
        for ek in range(int(min(ex) // 2000) * 2, int(max(ex) // 2000) * 2 + 2, 2):
            for nk in range(int(min(ny) // 2000) * 2, int(max(ny) // 2000) * 2 + 2, 2):
                f = os.path.join(CZ, f'{ek:03d}_{nk:04d}.tif')
                if os.path.exists(f):
                    out.append((f, None))
    elif source == 'PLE' and st['evrf']:
        # the surveys of 2018 on (EVRF2007): 1:5000 sheets, found by their bounds (EPSG:2180)
        ex, ny = st['to_pl'].transform(xs, ys)
        we0, we1, wn0, wn1 = min(ex), max(ex), min(ny), max(ny)
        seen = set()
        for ek in range(int(we0 // 10000), int(we1 // 10000) + 1):
            for nk in range(int(wn0 // 10000), int(wn1 // 10000) + 1):
                for e0, n0, e1, n1, f in st['evrf'].get((ek, nk), ()):
                    if f in seen or e1 < we0 or e0 > we1 or n1 < wn0 or n0 > wn1:
                        continue
                    seen.add(f)
                    out.append((f, -9999))
    elif source == 'PL' and os.path.isdir(PL):
        # the older coverage (KRON86): 10 km tiles, n{northing km}_e{easting km}, zeros beyond Poland
        ex, ny = st['to_pl'].transform(xs, ys)
        for ek in range(int(min(ex) // 10000) * 10, int(max(ex) // 10000) * 10 + 10, 10):
            for nk in range(int(min(ny) // 10000) * 10, int(max(ny) // 10000) * 10 + 10, 10):
                f = os.path.join(PL, f'n{nk:04d}_e{ek:04d}.tif')
                if os.path.exists(f):
                    out.append((f, 0))
    elif source == 'AT' and os.path.isdir(AT):
        # 50 km tiles in the target CRS, named by their south-west corner, no data beyond Austria
        for ek in range(int(bounds[0] // AT_TILE) * AT_TILE, int(bounds[2] // AT_TILE) * AT_TILE + 1, AT_TILE):
            for nk in range(int(bounds[1] // AT_TILE) * AT_TILE, int(bounds[3] // AT_TILE) * AT_TILE + 1, AT_TILE):
                f = os.path.join(AT, at_name(ek, nk))
                if os.path.exists(f):
                    out.append((f, None))
    elif source == 'FABDEM':
        # the 1″ model's 1° tiles (GEDTM30 in int cm, or FABDEM in 10° zips); a degree of sea has none
        # (the reader skips what fails to open)
        lon, lat = st['to_ll'].transform(xs, ys)
        for la in range(math.floor(min(lat)), math.floor(max(lat)) + 1):
            for lo in range(math.floor(min(lon)), math.floor(max(lon)) + 1):
                out.append((model_tile(la, lo), None if MODEL == 'gedtm30' else -9999))
    return out


def reader(x0, y0, res):
    """ `read(a, b, c, e)` for the grid with top-left corner (x0, y0) and `res` m cells. """

    def read(a, b, c, e, with_src=False):
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.transform import Affine
        st = _state()
        # a cell wider all round for the spike filter, and ISLAND more to judge the islands of data
        m = 1 + ISLAND
        a1, b1, c1, e1 = a - m, b + m, c - m, e + m
        h, w = b1 - a1, e1 - c1
        wx0, wy0 = x0 + c1 * res, y0 - a1 * res
        tr = Affine(res, 0, wx0, 0, -res, wy0)
        z = np.full((h, w), np.nan, np.float32)
        src = np.zeros((h, w), np.uint8)
        bounds = (wx0, wy0 - h * res, wx0 + w * res, wy0)

        def fill(part, code, source):
            nonlocal z
            part[~np.isfinite(part) | (part <= -100) | (part > 1e4)] = np.nan
            if source:
                corr = correction(st, source, wx0, wy0, res, h, w)
                part = part + corr
            new = np.isnan(z) & np.isfinite(part)
            z[new] = part[new]
            src[new] = code

        for code, source, resampling in ORDER:
            if source not in SOURCES or (code != 1 and not np.isnan(z).any()):
                continue
            fs = files(st, source, bounds)
            if not fs:
                continue
            part = np.full((h, w), np.nan, np.float32)
            for f, nodata in fs:
                try:
                    if f == SK:                 # the one big raster, kept open per process
                        t = _warp(st['sk'], part, tr, getattr(Resampling, resampling), nodata=nodata)
                    else:
                        with rasterio.open(f) as d:
                            t = _warp(d, part, tr, getattr(Resampling, resampling), nodata=nodata)
                except Exception:
                    if source == 'FABDEM':      # a degree with no tile: the sea
                        continue
                    raise
                part = np.where(np.isnan(part), t, part)
            fill(part, code, None if source == 'FABDEM' else source)
        drop_islands(z)
        z = z[ISLAND:-ISLAND, ISLAND:-ISLAND]
        z[np.isnan(z)] = 0
        z, n = despike(z)
        z = np.ascontiguousarray(z[1:-1, 1:-1])
        if not with_src:
            return z
        # which source each cell came from, 0 where the islands or the spike filter left none
        s = np.ascontiguousarray(src[m:-m, m:-m])
        s[z <= 0] = 0
        return z, s
    return read


def sources_at(xs, ys):
    """
    Which source each point of the target grid (xs, ys: cell centres) takes its height from, as
    the reader decides it: the first of Slovakia, Czechia, Poland, Austria with data there, else FABDEM (0: no data,
    when FABDEM is not read). Only the sources in SOURCES count.
    One batch per source: sampled at the points, not read again window by window.
    """
    import rasterio
    from pyproj import Transformer
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    out = np.full(len(xs), 4, np.uint8)
    todo = np.ones(len(xs), bool)

    def ok(v, nodata):
        v = np.asarray(v, float)
        good = np.isfinite(v) & (v > -100) & (v < 1e4)
        return good & (v != nodata) if nodata is not None else good

    if 'FABDEM' not in SOURCES:
        out[:] = 0
    if 'SK' in SOURCES:
        with rasterio.open(SK) as d:
            px, py = Transformer.from_crs(CRS, d.crs, always_xy=True).transform(xs, ys)
            v = heights(d, np.array([t[0] for t in d.sample(zip(px, py), masked=False)]))
            hit = ok(v, None)
            out[hit] = 1
            todo &= ~hit
    for code, folder, crs, size, name in ((2, CZ, 'EPSG:3045', 2000, lambda e, n: f'{e:03d}_{n:04d}.tif'),
                                          (5, None, 'EPSG:2180', None, None),
                                          (3, PL, 'EPSG:2180', 10000, lambda e, n: f'n{n:04d}_e{e:04d}.tif'),
                                          (6, AT, CRS, AT_TILE, lambda e, n: at_name(e * 1000, n * 1000))):
        if not todo.any() or {2: 'CZ', 5: 'PLE', 3: 'PL', 6: 'AT'}[code] not in SOURCES:
            continue
        if code == 5:
            cells = evrf_sheets()
            px, py = Transformer.from_crs(CRS, crs, always_xy=True).transform(xs, ys)
            for i in np.flatnonzero(todo):
                for e0, n0, e1, n1, f in cells.get((int(px[i] // 10000), int(py[i] // 10000)), ()):
                    if e0 <= px[i] <= e1 and n0 <= py[i] <= n1:
                        with rasterio.open(f) as d:
                            v = heights(d, next(d.sample([(px[i], py[i])], masked=False)), -9999)
                        if ok(v, None)[0]:
                            out[i] = 5
                            todo[i] = False
                            break
            continue
        if not os.path.isdir(folder):
            continue
        px, py = Transformer.from_crs(CRS, crs, always_xy=True).transform(xs, ys)
        step = size // 1000
        keys = {}
        for i in np.flatnonzero(todo):
            keys.setdefault((int(px[i] // size) * step, int(py[i] // size) * step), []).append(i)
        for (ek, nk), idx in keys.items():
            f = os.path.join(folder, name(ek, nk))
            if not os.path.exists(f):
                continue
            with rasterio.open(f) as d:
                v = heights(d, np.array([t[0] for t in d.sample([(px[i], py[i]) for i in idx], masked=False)]),
                            0 if code == 3 else None)
                hit = ok(v, None)
            for i, h in zip(idx, hit):
                if h:
                    out[i] = code
                    todo[i] = False
    return out


#: the one frame every run's tiles sit in (EPSG:3035, top-left corner and side in metres): a tile
#: is the same tile in every run, whatever its box, so that kept blocks serve the next one
FRAME_X0, FRAME_Y0, FRAME_SIDE = 0.0, 10_240_000.0, 10_240_000.0
STORE = path('store', 'joint', env='TREELINE_STORE')


def frame(res, tile):
    """ Cells a side of the frame: `tile` doubled until it covers FRAME_SIDE. """
    side = tile
    while side * res < FRAME_SIDE:
        side *= 2
    return side


def _stat(path):
    """ What says a file changed: its size and modification time (a zip's for a path inside one). """
    if path.startswith('/vsizip/'):
        path = path[len('/vsizip/'):].split('.zip/')[0] + '.zip'
    try:
        st = os.stat(path)
    except OSError:
        return f'{path} -'
    return f'{path} {st.st_size} {st.st_mtime_ns}'


def fingerprinter(x0, y0, res):
    """ `fingerprint(a, b, c, e)` for `catchments.run_store`: the files the reader opens for
    that window (with the margin it reads around it), each with its size and time; None if none. """
    m = 1 + ISLAND
    st = {}

    def fingerprint(a, b, c, e):
        if not st:                  # what `files` needs, without opening a raster in this process
            from pyproj import Transformer
            st.update(sk_bounds=_sk_bounds(), evrf=evrf_sheets(),
                      to_ll=Transformer.from_crs(CRS, 'EPSG:4326', always_xy=True),
                      to_cz=Transformer.from_crs(CRS, 'EPSG:3045', always_xy=True),
                      to_pl=Transformer.from_crs(CRS, 'EPSG:2180', always_xy=True))
        bounds = (x0 + (c - m) * res, y0 - (b + m) * res, x0 + (e + m) * res, y0 - (a - m) * res)
        parts = []
        for code, source, _ in ORDER:
            if source in SOURCES:
                parts += [f'{source} {_stat(f)}' for f, _ in files(st, source, bounds)]
        return '\n'.join(parts) if parts else None
    return fingerprint


def grid_fingerprinter(root, x0, y0):
    """ The same over a grid `joint_grid.py` built: the blocks the window reads. A block `extend`
    kept is a hard link to the old one, with its size and time. """
    import joint_grid

    def fingerprint(a, b, c, e):
        X0, X1, Ytop, Ybot = int(x0) + c, int(x0) + e, int(y0) - a, int(y0) - b
        parts = []
        for i in range(X0 // joint_grid.BLOCK, (X1 - 1) // joint_grid.BLOCK + 1):
            for j in range(Ybot // joint_grid.BLOCK, (Ytop - 1) // joint_grid.BLOCK + 1):
                f = joint_grid.name(root, 'z', i, j)
                if os.path.exists(f):
                    parts.append(_stat(f))
        return '\n'.join(parts) if parts else None
    return fingerprint


def salt(res, grid_dir=None):
    """ What else decides a tile: the reader's code and settings, GDAL, PROJ and its grids. """
    import inspect
    import joint_grid
    if grid_dir:
        return f'grid {res!r} ' + inspect.getsource(joint_grid.window)
    from pyproj.datadir import get_user_data_dir
    d = get_user_data_dir()
    grids = sorted(f'{f} {os.path.getsize(os.path.join(d, f))}' for f in os.listdir(d)) if os.path.isdir(d) else []
    return f'{joint_grid.code_fingerprint()} {res!r} {grids}'      # the sources: in each tile's files


def grid(s, n, w, e, res):
    """ The target grid covering the lon/lat box: top-left corner, rows, columns. """
    from pyproj import Transformer
    t = Transformer.from_crs('EPSG:4326', CRS, always_xy=True)
    lons = np.concatenate([np.linspace(w, e, 200), np.full(200, e), np.linspace(e, w, 200), np.full(200, w)])
    lats = np.concatenate([np.full(200, s), np.linspace(s, n, 200), np.full(200, n), np.linspace(n, s, 200)])
    xs, ys = t.transform(lons, lats)
    x0, x1 = math.floor(min(xs) / 1000) * 1000, math.ceil(max(xs) / 1000) * 1000
    y0, y1 = math.floor(min(ys) / 1000) * 1000, math.ceil(max(ys) / 1000) * 1000
    return x0, y1, int((y1 - y0) / res), int((x1 - x0) / res)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('bbox', help='S,N,W,E in degrees')
    ap.add_argument('out')
    ap.add_argument('--res', type=float, default=1.0)
    ap.add_argument('--threshold', type=float, default=30.0)
    ap.add_argument('--jobs', type=int, default=12)
    ap.add_argument('--tile', type=int, default=5000)
    ap.add_argument('--line-cutoff', type=float, default=100.0)
    ap.add_argument('--sources', default=','.join(ALL_SOURCES), help='which to read, comma-separated')
    ap.add_argument('--grid', help='read the terrain built by joint_grid.py in this folder (its sources)')
    ap.add_argument('--store', default=STORE, help=f'where the blocks are kept (default {STORE})')
    a = ap.parse_args()
    SOURCES.clear()
    SOURCES.update(v.strip() for v in a.sources.split(','))
    if SOURCES - set(ALL_SOURCES):
        ap.error(f'unknown sources: {SOURCES - set(ALL_SOURCES)}')
    s, n, w, e = (float(v) for v in a.bbox.split(','))
    bx0, by0, bH, bW = grid(s, n, w, e, a.res)
    if a.grid:
        import joint_grid
        meta = joint_grid.describe(a.grid)
        if a.res != meta['res']:
            ap.error(f'the grid is at {meta["res"]} m')
        if bx0 < meta['x0'] or by0 > meta['y0'] or bx0 + bW > meta['x0'] + meta['W'] or by0 - bH < meta['y0'] - meta['H']:
            ap.error(f'the box reaches beyond the grid built for {meta["bbox"]}')
        SOURCES.clear()
        SOURCES.update(meta['sources'])
        print(f'reading the grid in {a.grid}: {",".join(meta["sources"])}', flush=True)
    # the frame, and the tiles of it the box touches
    x0, y0 = FRAME_X0, FRAME_Y0
    H = W = frame(a.res, a.tile)
    r0, c0 = round((y0 - by0) / a.res), round((bx0 - x0) / a.res)
    tiles = [(i, j) for i in range(r0 // a.tile, (r0 + bH - 1) // a.tile + 1)
             for j in range(c0 // a.tile, (c0 + bW - 1) // a.tile + 1)]
    print(f'{len(tiles)} tiles of {a.tile} × {a.tile} cells of {a.res:g} m in {CRS}, {a.jobs} workers, '
          f'blocks kept in {a.store}', flush=True)
    t0 = time.time()
    read = joint_grid.grid_reader(a.grid, int(x0), int(y0)) if a.grid else reader(x0, y0, a.res)
    fingerprint = grid_fingerprinter(a.grid, x0, y0) if a.grid else fingerprinter(x0, y0, a.res)
    import hashlib
    name = f'{os.path.splitext(os.path.basename(a.out))[0]}-{hashlib.sha256(os.path.abspath(a.out).encode()).hexdigest()[:8]}'
    rec, stats = run_store(read, fingerprint, a.store, H, W, a.tile, a.threshold, tiles=tiles,
                           salt=salt(a.res, a.grid), log=lambda m: print(m, flush=True), jobs=a.jobs, name=name)
    par, ta, tb = walk(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'])
    proven = walk_settled(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], rec['border'], rec['weak'])
    line = walk_line(rec['top'], rec['col'], rec['hz'], rec['cz'], rec['far'], a.line_cutoff)
    print(f'{time.time() - t0:.0f} s: {len(rec["top"])} tops of P >= {a.threshold:g}, '
          f'{int((~rec["border"]).sum())} settled, {int(proven.sum())} with their parent proven; {len(ta)} ties',
          flush=True)
    np.savez_compressed(os.path.splitext(a.out)[0] + '.npz', **{k: v for k, v in rec.items() if isinstance(v, np.ndarray)},
                        x0=x0, y0=y0, res=a.res, H=H, W=W, threshold=a.threshold)
    from pyproj import Transformer
    back = Transformer.from_crs(CRS, 'EPSG:4326', always_xy=True)

    def ll(g):
        r, c = divmod(int(g), W)
        lo, la = back.transform(x0 + (c + 0.5) * a.res, y0 - (r + 0.5) * a.res)
        return [round(la, 7), round(lo, 7)]

    # which source each top and col stands on
    cells = np.concatenate([rec['top'], rec['col'][rec['col'] >= 0]])
    rr, cc = np.divmod(cells, W)
    xs, ys = x0 + (cc + 0.5) * a.res, y0 - (rr + 0.5) * a.res
    codes = joint_grid.sources_at(a.grid, xs, ys) if a.grid else sources_at(xs, ys)
    src_code = dict(zip(cells.tolist(), codes.tolist()))
    src_of = lambda g: NAMES[src_code[int(g)]]
    prom = np.where(rec['col'] >= 0, rec['hz'] - rec['cz'], rec['hz'])
    res = []
    for i in range(len(rec['top'])):
        x = dict(top=ll(rec['top'][i]), h=round(float(rec['hz'][i]), 2), prom=round(float(prom[i]), 2),
                 border=bool(rec['border'][i]), parent_proven=bool(proven[i]), source=src_of(rec['top'][i]))
        if rec['col'][i] >= 0:
            x.update(col=ll(rec['col'][i]), col_h=round(float(rec['cz'][i]), 2), col_source=src_of(rec['col'][i]))
        if par[i] >= 0:
            x['parent'] = ll(par[i])
        if line[i] >= 0:
            x['line'] = ll(line[i])
        res.append(x)
    st = {k: v for k, v in stats.items() if k != 'tiles'}
    st.update(tops=sum(t['tops'] for t in stats['tiles'] if t), ties=[[ll(p), ll(q)] for p, q in zip(ta, tb)],
              settled=int((~rec['border']).sum()), parent_proven=int(proven.sum()), store=a.store, box=a.bbox,
              line_cutoff=a.line_cutoff, crs=CRS, res=a.res,
              sources=sorted(SOURCES), grid=a.grid,
              source=f'DMR 5.0 (SK), DMR 5G (CZ), NMT (PL), ALS-DGM (AT, BEV), {model_name()} beyond them; '
                     'heights EGM2008')
    with open(a.out, 'w') as f:
        json.dump(dict(stats=st, summits=res), f)
    if a.grid:
        joint_grid.close()
    print(f'-> {a.out}', flush=True)


if __name__ == '__main__':
    main()
