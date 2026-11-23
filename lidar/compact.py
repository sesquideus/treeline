"""
The lidar rasters as integers of a fixed unit (centimetres by default): 3–5× smaller than the
float32 the agencies ship, with nothing lost but digits far below the survey's precision.

    cd lidar && uv run --project .. --group lidar python compact.py SRC DST [--unit 0.01] [--nodata 0] [--jobs 16]

SRC is one GeoTIFF or a folder of them (searched recursively); DST mirrors it, file for file.
Each cell becomes round(z / unit) as int32, tiled 512, deflate with horizontal differencing
(predictor 2, which turns smooth terrain into small numbers); the unit is the file's GDAL scale,
so `catchments.heights` (and any GIS) reads metres back. No data — the file's own nodata, NaN,
and `--nodata` where the source uses a value it does not declare (KRON86's zeros) — becomes
INT32_MIN, declared. Blocks with no data at all are not written (sparse), and read as no data.

Verified as written, every cell: each height comes back within half a unit, no data stays no
data and nothing else becomes it; then the finished file is read back whole and compared with
what was meant to be written, block by block, before it takes its final name. A file whose
final name exists is done and skipped, so an interrupted run resumes.

Measured (2000 × 2000 windows, cm): SK 0.57, AT 0.92, CZ 0.72 MB/km² against 2.7–3.9 as shipped.
The digits dropped are interpolation output: AT and our CZ rasters are continuous, SK is on a
0.1 mm grid, PL and the Czech LAZ points are whole cm, and airborne lidar is good to centimetres.
"""
import argparse
import os
import sys
import time
import zlib

import numpy as np

NODATA = np.iinfo(np.int32).min
BLOCK = 512
STEP = 8 * BLOCK                       # cells a side per window read and written


def windows(H, W):
    for r in range(0, H, STEP):
        for c in range(0, W, STEP):
            yield r, c, min(STEP, H - r), min(STEP, W - c)


def quantize(z, unit, nodata):
    """ (int32 values, stats) for float heights `z`. """
    z = z.astype(np.float64)
    bad = ~np.isfinite(z)
    for nd in nodata:
        if nd is not None:
            bad |= z == nd
    q = np.full(z.shape, NODATA, np.int32)
    good = ~bad
    v = np.round(z[good] / unit)
    if v.size and (v.min() <= NODATA or v.max() >= np.iinfo(np.int32).max):
        raise ValueError(f'heights {z[good].min()}..{z[good].max()} do not fit int32 at unit {unit}')
    q[good] = v.astype(np.int32)
    err = np.abs(q[good] * unit - z[good])
    stats = dict(cells=int(good.sum()), nodata=int(bad.sum()),
                 max_err=float(err.max()) if err.size else 0.0, sq_err=float((err ** 2).sum()))
    # the checks the docstring promises, on every cell
    if stats['max_err'] > unit / 2 + 1e-9 * max(1.0, float(np.abs(z[good]).max(initial=0))):
        raise AssertionError(f'error {stats["max_err"]} beyond half a unit')
    if ((q == NODATA) != bad).any():
        raise AssertionError('no data changed')
    return q, stats


def convert(src, dst, unit, extra_nodata, threads):
    """ One file; returns its stats. Writes `dst` + '.part', verifies, renames. """
    import rasterio
    from rasterio.windows import Window
    t0 = time.time()
    part = dst + '.part'
    os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
    total = dict(cells=0, nodata=0, max_err=0.0, sq_err=0.0, blocks_skipped=0)
    crc = {}
    with rasterio.open(src, num_threads=threads) as s:
        if s.count != 1:
            raise ValueError(f'{src}: {s.count} bands')
        profile = dict(driver='GTiff', width=s.width, height=s.height, count=1, dtype='int32',
                       crs=s.crs, transform=s.transform, nodata=NODATA, tiled=True,
                       blockxsize=BLOCK, blockysize=BLOCK, compress='deflate', predictor=2, zlevel=6,
                       bigtiff='IF_SAFER', sparse_ok=True, num_threads=threads)
        with rasterio.open(part, 'w', **profile) as d:
            d.scales, d.offsets, d.units = (unit,), (0.0,), ('metre',)
            ws = list(windows(s.height, s.width))
            bar = None
            if len(ws) > 16:
                from tqdm import tqdm
                bar = tqdm(total=len(ws), unit='window', file=sys.stdout, dynamic_ncols=True,
                           desc=os.path.basename(src))
            for r, c, h, w in ws:
                q, st = quantize(s.read(1, window=Window(c, r, w, h)), unit, (s.nodata, extra_nodata))
                for k in ('cells', 'nodata'):
                    total[k] += st[k]
                total['sq_err'] += st['sq_err']
                total['max_err'] = max(total['max_err'], st['max_err'])
                if st['cells']:
                    d.write(q, 1, window=Window(c, r, w, h))
                else:
                    total['blocks_skipped'] += 1
                crc[(r, c)] = zlib.crc32(q.tobytes())
                if bar:
                    bar.update(1)
            if bar:
                bar.close()
    # read back what was written, every window
    with rasterio.open(part, num_threads=threads) as d:
        if d.scales[0] != unit or d.nodata != NODATA or d.dtypes[0] != 'int32':
            raise AssertionError(f'{part}: metadata {d.scales} {d.nodata} {d.dtypes}')
        with rasterio.open(src) as s:
            if d.crs != s.crs or d.transform != s.transform or (d.height, d.width) != (s.height, s.width):
                raise AssertionError(f'{part}: georeferencing differs from {src}')
        for (r, c), want in crc.items():
            w, h = min(STEP, d.width - c), min(STEP, d.height - r)
            if zlib.crc32(d.read(1, window=Window(c, r, w, h)).tobytes()) != want:
                raise AssertionError(f'{part}: window at row {r}, col {c} reads back differently')
    os.replace(part, dst)
    total.update(src=src, bytes_in=os.path.getsize(src), bytes_out=os.path.getsize(dst),
                 seconds=time.time() - t0)
    return total


def _job(a):
    return convert(*a)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--unit', type=float, default=0.01, help='metres per integer step (0.01: cm)')
    ap.add_argument('--nodata', type=float, default=None,
                    help='a value the source uses for no data without declaring it')
    ap.add_argument('--jobs', type=int, default=16)
    a = ap.parse_args()

    if os.path.isfile(a.src):
        pairs = [(a.src, a.dst if a.dst.endswith('.tif') else os.path.join(a.dst, os.path.basename(a.src)))]
    else:
        pairs = []
        for root, _, files in os.walk(a.src):
            for f in sorted(files):
                if f.lower().endswith(('.tif', '.tiff')):
                    s = os.path.join(root, f)
                    pairs.append((s, os.path.join(a.dst, os.path.relpath(s, a.src))))
    todo = [(s, d) for s, d in pairs if not os.path.exists(d)]
    print(f'{len(pairs)} files, {len(pairs) - len(todo)} done already, unit {a.unit:g} m, {a.jobs} jobs',
          flush=True)

    total = dict(cells=0, nodata=0, max_err=0.0, sq_err=0.0, bytes_in=0, bytes_out=0)

    def add(st):
        for k in ('cells', 'nodata', 'sq_err', 'bytes_in', 'bytes_out'):
            total[k] += st[k]
        total['max_err'] = max(total['max_err'], st['max_err'])

    t0 = time.time()
    if len(todo) == 1:                              # one big file: GDAL's threads compress
        add(convert(*todo[0], a.unit, a.nodata, a.jobs))
    elif todo:
        import multiprocessing
        from tqdm import tqdm
        args = [(s, d, a.unit, a.nodata, 1) for s, d in todo]
        with multiprocessing.get_context('fork').Pool(a.jobs) as pool, \
                tqdm(total=len(args), unit='file', file=sys.stdout, dynamic_ncols=True) as bar:
            for st in pool.imap_unordered(_job, args):
                add(st)
                bar.update(1)
    if total['cells']:
        rms = (total['sq_err'] / total['cells']) ** 0.5
        print(f'{time.time() - t0:8.0f} s   {total["cells"]} cells, {total["nodata"]} no data\n'
              f'{total["bytes_in"] / 1e9:8.2f} GB in, {total["bytes_out"] / 1e9:.2f} GB out '
              f'({total["bytes_out"] / max(1, total["bytes_in"]):.3f})\n'
              f'   error: max {1000 * total["max_err"]:.3f} mm, rms {1000 * rms:.3f} mm '
              f'(uniform rounding: {1000 * a.unit / 12 ** 0.5:.3f})', flush=True)


if __name__ == '__main__':
    main()
