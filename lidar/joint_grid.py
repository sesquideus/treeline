"""
The joint terrain built once: `joint.py`'s reader (every source on EPSG:3035 at 1 m, heights in
EGM2008, islands of data and spikes filtered) written to disk in blocks, so that a run reads it
rather than deriving it again in every tile.

    cd lidar && uv run --project .. --group lidar python joint_grid.py build S,N,W,E DIR [--sources SK,CZ,AT] [--jobs 16]
    cd lidar && uv run --project .. --group lidar python joint_grid.py verify DIR [--cells 20000]
    cd lidar && uv run --project .. --group lidar python joint.py S,N,W,E OUT.json --grid DIR [--threshold 10] [--jobs 16]

Why. Reading is 25–65 % of a tile's time (reprojecting, the geoid correction, the filters), and
every tile does it again for its one-cell halo. Worse, two windows do not read a shared cell
quite alike: the geoid correction is interpolated per window (fractions of a millimetre) and
GDAL's approximate reprojection picks the neighbouring source cell for about 0.3 % of cells, in
a pattern that depends on the window. Read from the grid, every cell has one value.

Layout. DIR/z/E{e}N{n}.tif holds the heights of the 4 km block whose south-west corner is (e, n)
km in EPSG:3035, as int32 centimetres (the GDAL scale says 0.01; deflate, predictor 2; INT32_MIN
no data); DIR/src/ the same block's source codes (joint.NAMES, 0 no data). A block with no data
at all is not written. DIR/grid.json says what was built: box, sources, unit; DIR/blocks.log
lists the blocks finished (with their cells and bytes), so an interrupted build resumes. Blocks
sit at absolute positions, so any run box inside the built one reads them.

Nothing is lost and nothing touched. The build only reads the sources and only writes under DIR,
which may not be, contain, or lie inside a source folder; it deletes nothing but its own
unfinished `.part` files. Every cell is checked before it is written (within half a centimetre of
what the reader gave, no data exactly where the reader had none), and every block file is read
back and compared before it gets its final name. `verify` then checks the grid against the
sources independently: random cells, each source sampled at the exact point with PROJ's exact
transform and the geoid correction computed at that point, no warping.
"""
import argparse
import json
import os
import sys
import time
import zlib

import numpy as np

import joint
from compact import NODATA, quantize

BLOCK = 4000                    # cells (= metres at 1 m) a side
UNIT = 0.01


def name(root, kind, i, j):
    """ Block (i, j) covers x in [i·BLOCK, (i+1)·BLOCK), y in [j·BLOCK, (j+1)·BLOCK) of EPSG:3035. """
    return os.path.join(root, kind, f'E{i * BLOCK // 1000:05d}N{j * BLOCK // 1000:05d}.tif')


def source_folders():
    """ Each source's whole dataset folder — the one directly under a `treeline` folder (ALS_AT,
    not just ALS_AT/tiles) — as given and with symlinks resolved. """
    from catchments import FABDEM
    out = set()
    for f in (os.path.dirname(joint.SK), joint.CZ, joint.PL, joint.PLE, joint.AT, FABDEM):
        for p in (os.path.abspath(f), os.path.realpath(f)):
            q = p
            while os.path.dirname(q) != q and os.path.basename(os.path.dirname(q)) != 'treeline':
                q = os.path.dirname(q)
            out.add(q if os.path.dirname(q) != q else p)
    return sorted(out)


def check_safe(root):
    """ Refuse a DIR that is, contains, or lies inside a source's dataset folder. """
    for r in {os.path.abspath(root), os.path.realpath(root)}:
        for f in source_folders():
            if r == f or r.startswith(f + os.sep) or f.startswith(r + os.sep):
                raise SystemExit(f'{root} overlaps the source folder {f}: refusing to write there')


def blocks_for(x0, y0, H, W):
    """ The blocks covering the grid with top-left (x0, y0), H × W cells of 1 m. """
    i0, i1 = x0 // BLOCK, (x0 + W - 1) // BLOCK
    j0, j1 = (y0 - H) // BLOCK, (y0 - 1) // BLOCK
    return [(i, j) for j in range(j1, j0 - 1, -1) for i in range(i0, i1 + 1)]


def _profile(i, j, dtype, **kw):
    from rasterio.transform import Affine
    return dict(driver='GTiff', width=BLOCK, height=BLOCK, count=1, dtype=dtype, crs=joint.CRS,
                transform=Affine(1, 0, i * BLOCK, 0, -1, (j + 1) * BLOCK), tiled=True, blockxsize=512,
                blockysize=512, compress='deflate', predictor=2, zlevel=6, **kw)


def _write(path, arr, profile, scale=None):
    """ Write, read back, compare, then rename into place. """
    import rasterio
    part = path + '.part'
    with rasterio.open(part, 'w', **profile) as d:
        if scale is not None:
            d.scales, d.offsets, d.units = (scale,), (0.0,), ('metre',)
        d.write(arr, 1)
    with rasterio.open(part) as d:
        back = d.read(1)
        if scale is not None and (d.scales[0] != scale or d.nodata != NODATA):
            raise AssertionError(f'{part}: metadata')
    if zlib.crc32(back.tobytes()) != zlib.crc32(arr.tobytes()):
        raise AssertionError(f'{part}: reads back differently')
    os.replace(part, path)
    return os.path.getsize(path)


def _block(args):
    """ One block: read as a run would, check, write. Returns (i, j, cells, bytes). """
    root, i, j = args
    read = joint.reader(i * BLOCK, (j + 1) * BLOCK, 1.0)
    z, s = read(0, BLOCK, 0, BLOCK, with_src=True)
    zf = z.astype(np.float64)
    zf[z <= 0] = np.nan                          # the engine's no data
    q, st = quantize(zf, UNIT, ())              # raises unless every cell is within half a unit
    if not st['cells']:
        return i, j, 0, 0
    if ((q == NODATA) != (s == 0)).any():
        raise AssertionError(f'block {i},{j}: source codes and heights disagree on no data')
    n = _write(name(root, 'z', i, j), q, _profile(i, j, 'int32', nodata=NODATA), scale=UNIT)
    n += _write(name(root, 'src', i, j), s, _profile(i, j, 'uint8'))
    return i, j, st['cells'], n


def build(bbox, root, sources, jobs):
    check_safe(root)
    os.makedirs(os.path.join(root, 'z'), exist_ok=True)
    os.makedirs(os.path.join(root, 'src'), exist_ok=True)
    s, n, w, e = bbox
    x0, y0, H, W = joint.grid(s, n, w, e, 1.0)
    meta = dict(bbox=list(bbox), x0=x0, y0=y0, H=H, W=W, res=1.0, block=BLOCK, unit=UNIT, crs=joint.CRS,
                sources=sorted(sources), island=joint.ISLAND)
    path = os.path.join(root, 'grid.json')
    if os.path.exists(path):
        old = json.load(open(path))
        if {k: old.get(k) for k in meta} != meta:
            raise SystemExit(f'{path} describes another build: {old}\nUse a new DIR.')
    else:
        json.dump(meta, open(path, 'w'), indent=1)
    for kind in ('z', 'src'):                    # only our own leftovers
        for f in os.listdir(os.path.join(root, kind)):
            if f.endswith('.part'):
                os.remove(os.path.join(root, kind, f))
    log = os.path.join(root, 'blocks.log')
    done = set()
    if os.path.exists(log):
        for line in open(log):
            bi, bj = line.split()[:2]
            done.add((int(bi), int(bj)))
    blocks = blocks_for(x0, y0, H, W)
    todo = [b for b in blocks if b not in done]
    print(f'{len(blocks)} blocks of {BLOCK} m, {len(done)} done already, sources {",".join(sorted(sources))}, '
          f'{jobs} workers', flush=True)
    joint.SOURCES.clear()
    joint.SOURCES.update(sources)
    import multiprocessing
    from tqdm import tqdm
    cells = written = 0
    with open(log, 'a') as L, multiprocessing.get_context('fork').Pool(jobs) as pool, \
            tqdm(total=len(blocks), initial=len(done), unit='block', file=sys.stdout, mininterval=0,
                 smoothing=0.05, dynamic_ncols=True) as bar:
        for i, j, c, b in pool.imap_unordered(_block, [(root, i, j) for i, j in todo]):
            L.write(f'{i} {j} {c} {b}\n')
            L.flush()
            cells += c
            written += b
            bar.set_postfix_str(f'{written / 1e9:.2f} GB', refresh=False)
            bar.update(1)
    print(f'{cells} cells with data, {written / 1e9:.2f} GB written', flush=True)


# ------------------------------------------------------------------ reading

_OPEN = {}


def _open(path):
    import rasterio
    key = (os.getpid(), path)
    d = _OPEN.get(key)
    if d is None:
        if len(_OPEN) > 256:
            for k in list(_OPEN)[:128]:
                _OPEN.pop(k).close()
        d = _OPEN[key] = rasterio.open(path) if os.path.exists(path) else False
    return d


def window(root, kind, x0, y0, a, b, c, e):
    """ Rows a..b, columns c..e of the grid with top-left (x0, y0): heights in metres (0 no data)
    or source codes. """
    from rasterio.windows import Window
    out = np.zeros((b - a, e - c), np.float32 if kind == 'z' else np.uint8)
    X0, X1, Ytop, Ybot = x0 + c, x0 + e, y0 - a, y0 - b
    for i in range(X0 // BLOCK, (X1 - 1) // BLOCK + 1):
        for j in range(Ybot // BLOCK, (Ytop - 1) // BLOCK + 1):
            d = _open(name(root, kind, i, j))
            if not d:
                continue
            bx0, bx1 = max(X0, i * BLOCK), min(X1, (i + 1) * BLOCK)
            by1, by0 = min(Ytop, (j + 1) * BLOCK), max(Ybot, j * BLOCK)
            v = d.read(1, window=Window(bx0 - i * BLOCK, (j + 1) * BLOCK - by1, bx1 - bx0, by1 - by0))
            if kind == 'z':
                v = np.where(v == NODATA, 0, v.astype(np.float32) * np.float32(UNIT))
            out[Ytop - by1:Ytop - by0, bx0 - X0:bx1 - X0] = v
    return out


def grid_reader(root, x0, y0):
    """ `read(a, b, c, e)` over the built grid, for `catchments.run`. """
    return lambda a, b, c, e: window(root, 'z', x0, y0, a, b, c, e)


def sources_at(root, xs, ys):
    """ The source code of each point (cell centres in EPSG:3035), from the grid. """
    out = np.zeros(len(xs), np.uint8)
    for k, (x, y) in enumerate(zip(xs, ys)):
        c, r = int(np.floor(x)), int(np.ceil(y))
        out[k] = window(root, 'src', c, r, 0, 1, 0, 1)[0, 0]
    return out


def describe(root):
    return json.load(open(os.path.join(root, 'grid.json')))


# ------------------------------------------------------------------ verifying

def _exact(source, x, y):
    """ The source's own height at the point (no warping: PROJ's exact transform to its CRS, its
    nearest cell and the 3 × 3 around it), carried to EGM2008 at that point. None if no file. """
    import rasterio
    from pyproj import Transformer
    from catchments import heights
    st = joint._state()
    lon, lat = st['to_ll'].transform(x, y)
    if source == 'SK':
        path, crs = joint.SK, None
    elif source == 'AT':
        path = os.path.join(joint.AT, joint.at_name(int(x // joint.AT_TILE) * joint.AT_TILE,
                                                    int(y // joint.AT_TILE) * joint.AT_TILE))
    elif source == 'CZ':
        ex, ny = st['to_cz'].transform(x, y)
        path = os.path.join(joint.CZ, f'{int(ex // 2000) * 2:03d}_{int(ny // 2000) * 2:04d}.tif')
    else:
        return None
    if not os.path.exists(path):
        return None
    with rasterio.open(path) as d:
        px, py = Transformer.from_crs(joint.CRS, d.crs, always_xy=True).transform(x, y)
        r, c = d.index(px, py)
        from rasterio.windows import Window
        v = heights(d, d.read(1, window=Window(c - 1, r - 1, 3, 3), boundless=True,
                              fill_value=d.nodata if d.nodata is not None else np.nan))
    _, _, hz = st['egm'][source].transform(lon, lat, 0.0)
    return v + np.float32(hz)


def verify(root, cells, seed=1):
    meta = describe(root)
    rng = np.random.default_rng(seed)
    log = [line.split() for line in open(os.path.join(root, 'blocks.log'))]
    full = [(int(i), int(j)) for i, j, c, _ in log if int(c) > 0]
    if not full:
        raise SystemExit('no blocks with data')
    code = {1: 'SK', 2: 'CZ', 6: 'AT'}
    counts = dict(same=0, neighbour=0, lower=0, other=0, nodata_agrees=0, nodata_dropped=0, skipped=0)
    worst = []
    from tqdm import tqdm
    for _ in tqdm(range(cells), unit='cell', file=sys.stdout, dynamic_ncols=True):
        i, j = full[rng.integers(len(full))]
        c, r = rng.integers(BLOCK), rng.integers(BLOCK)
        x, y = i * BLOCK + c + 0.5, (j + 1) * BLOCK - r - 0.5
        g = window(root, 'z', i * BLOCK, (j + 1) * BLOCK, r, r + 1, c, c + 1)[0, 0]
        s = window(root, 'src', i * BLOCK, (j + 1) * BLOCK, r, r + 1, c, c + 1)[0, 0]
        if g > 0:
            if s not in code:
                counts['skipped'] += 1
                continue
            v = _exact(code[s], x, y)
            if v is None:
                counts['other'] += 1
                worst.append((x, y, g, None))
                continue
            tol = UNIT / 2 + 0.003           # rounding, and the correction interpolated over 500 m
            if abs(v[1, 1] - g) <= tol:
                counts['same'] += 1
            elif np.nanmin(np.abs(v - g)) <= tol:
                counts['neighbour'] += 1      # GDAL's approximate reprojection took the cell beside it
            elif g < v[1, 1]:
                counts['lower'] += 1          # the spike filter
            else:
                counts['other'] += 1
                worst.append((x, y, float(g), float(v[1, 1])))
        else:
            vals = [_exact(sname, x, y) for sname in meta['sources'] if sname in ('SK', 'CZ', 'AT')]
            has = [v for v in vals if v is not None and np.isfinite(v[1, 1]) and v[1, 1] > 0]
            counts['nodata_dropped' if has else 'nodata_agrees'] += 1
    print(json.dumps(counts))
    for w in worst[:20]:
        print('   unexplained at', w)
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('build')
    p.add_argument('bbox', help='S,N,W,E in degrees')
    p.add_argument('dir')
    p.add_argument('--sources', default='SK,CZ,AT')
    p.add_argument('--jobs', type=int, default=16)
    p = sub.add_parser('verify')
    p.add_argument('dir')
    p.add_argument('--cells', type=int, default=20000)
    a = ap.parse_args()
    if a.cmd == 'build':
        sources = {v.strip() for v in a.sources.split(',')}
        if sources - set(joint.ALL_SOURCES):
            ap.error(f'unknown sources: {sources - set(joint.ALL_SOURCES)}')
        t0 = time.time()
        build(tuple(float(v) for v in a.bbox.split(',')), a.dir, sources, a.jobs)
        print(f'{time.time() - t0:.0f} s', flush=True)
    else:
        verify(a.dir, a.cells)


if __name__ == '__main__':
    main()
