"""
Czechia's DMR 5G (ČÚZK, CC BY 4.0) as a 1 m raster: each 2 × 2 km tile of LAZ ground points,
triangulated and read off linearly, as ČÚZK interpolates its own model, with a 50 m strip of the
neighbouring tiles' points so that tiles meet without a seam. ETRS89/TM33N (EPSG:3045), heights Bpv
as the points have them; nodata NaN beyond the points (the border).

    cd lidar && uv run --project .. --group lidar python dmr5g_raster.py SRC_DIR OUT_DIR [--res 1] [--jobs 12]

SRC_DIR holds the ATOM service's zips (name `EEE_NNNN.zip`: the tile's south-west corner in km,
easting and northing); OUT_DIR gets one GeoTIFF per tile, the same name (deflate, float32), which
a reader finds by position. A rerun skips the tiles already written.
"""
import argparse
import glob
import io
import os
import re
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

import numpy as np

TILE = 2000.0          # m
MARGIN = 50.0          # m of the neighbours' points
CRS = 'EPSG:3045'


def read_points(path):
    """ x, y, z of a zipped LAZ tile, or None if it is missing. """
    import laspy
    if not os.path.exists(path):
        return None
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(('.laz', '.las')))
        las = laspy.read(io.BytesIO(z.read(name)))
    return np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)


def raster_tile(args):
    src, out_dir, e, n, res = args
    out = os.path.join(out_dir, f'{e:03d}_{n:04d}.tif')
    if os.path.exists(out):
        return 'have'
    import rasterio
    from rasterio.transform import from_origin
    from scipy.interpolate import LinearNDInterpolator
    x0, y0 = e * 1000.0, n * 1000.0
    xs, ys, zs = [], [], []
    for de in (-2, 0, 2):
        for dn in (-2, 0, 2):
            p = read_points(os.path.join(src, f'{e + de:03d}_{n + dn:04d}.zip'))
            if p is None:
                continue
            x, y, z = p
            keep = (x >= x0 - MARGIN) & (x <= x0 + TILE + MARGIN) & (y >= y0 - MARGIN) & (y <= y0 + TILE + MARGIN)
            xs.append(x[keep]); ys.append(y[keep]); zs.append(z[keep])
    x, y, z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
    if len(x) < 3:
        return 'empty'
    size = int(TILE / res)
    cx = x0 + (np.arange(size) + 0.5) * res
    cy = y0 + TILE - (np.arange(size) + 0.5) * res
    gx, gy = np.meshgrid(cx, cy)
    f = LinearNDInterpolator(np.column_stack([x, y]), z)
    grid = f(gx, gy).astype(np.float32)
    with rasterio.open(out + '.part', 'w', driver='GTiff', height=size, width=size, count=1, dtype='float32',
                       crs=CRS, transform=from_origin(x0, y0 + TILE, res, res), nodata=np.nan,
                       compress='deflate', predictor=3, tiled=True, blockxsize=512, blockysize=512) as d:
        d.write(grid, 1)
    os.replace(out + '.part', out)
    return 'made'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--res', type=float, default=1.0)
    ap.add_argument('--jobs', type=int, default=12)
    ap.add_argument('--only', help='a box of tiles EMIN,EMAX,NMIN,NMAX in km, for a test')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    tiles = sorted((int(m[1]), int(m[2])) for f in glob.glob(os.path.join(a.src, '*.zip'))
                   if (m := re.match(r'(\d+)_(\d+)\.zip$', os.path.basename(f))))
    if a.only:
        e0, e1, n0, n1 = (int(v) for v in a.only.split(','))
        tiles = [(e, n) for e, n in tiles if e0 <= e <= e1 and n0 <= n <= n1]
    from tqdm import tqdm
    counts = {'made': 0, 'have': 0, 'empty': 0}
    t0 = time.time()
    with ProcessPoolExecutor(a.jobs) as pool:
        for r in tqdm(pool.map(raster_tile, [(a.src, a.out, e, n, a.res) for e, n in tiles], chunksize=4),
                      total=len(tiles), unit='tile', desc='DMR 5G'):
            counts[r] += 1
    print(counts, f'{time.time() - t0:.0f} s')


if __name__ == '__main__':
    main()
