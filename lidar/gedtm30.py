"""
GEDTM30 v1.2 (OpenGeoHub, CC BY 4.0): a global bare-earth terrain model at 1″, EGM2008, published as
one cloud-optimised GeoTIFF. Read once, window by window, into 1° tiles in integer centimetres laid out as FABDEM's
(3600 × 3600, cell centres on whole arcseconds, the first row on the tile's north edge), so that
`catchments.py world` and `burn_match.py` read it in FABDEM's place (TREELINE_MODEL=gedtm30).

    cd lidar && uv run --project .. --group lidar python gedtm30.py fetch --bbox 44,53,21,41 [--jobs 6]
    cd lidar && uv run --project .. --group lidar python gedtm30.py fetch --land        # the whole world's land

TRAP: the file declares a scale of 0.1, but its values are metres already (Hoverla reads 2053 m,
its surveyed height 2061 m): the scale is ignored, and the tiles carry none. Its no-data value
(3.4e38, the sea) is written as NaN. The COG's grid starts 4.5″ west and north of whole degrees, so
its cell centres fall on whole arcseconds, as FABDEM's do: a tile is a plain window, no resampling.

Tiles: TREELINE_GEDTM30, else $TREELINE_DATA/intcm/GEDTM30, int32 cm with scale 0.01 (read them through
`catchments.read_model`), named as FABDEM's (N48E024.tif); the float read goes through TREELINE_GEDTM30_TMP. Asks as treeline-client/0.1. Resumable: a tile on disk that reads back whole is
kept; each is written under a temporary name and renamed when complete. A failed read is retried
with backoff (and /vsicurl retries on its own, GDAL_HTTP_MAX_RETRY), then in 16 smaller windows;
a tile that still fails is reported and the rest go on, so a rerun fetches only what is missing.
"""
import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

URL = ('/vsicurl/https://s3.opengeohub.org/global/dtm/v1.2/'
       'gedtm_rf_m_30m_s_20060101_20151231_go_epsg.4326.3855_v1.2.tif')
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data import path as _path  # noqa: E402

# Tiles in integer centimetres (lidar/compact.py), as all terrain: $TREELINE_DATA/intcm/GEDTM30. The model
# is centimetres already (float32 noise aside), so nothing is lost: 21 MB a tile as float, 17 MB as int cm.
DIR = _path('intcm', 'GEDTM30', env='TREELINE_GEDTM30')
# Each tile is read into a float file here first (the fast SSD, beside the repository: download/ is
# git-ignored), converted into DIR (the large disk), and dropped.
TMP = os.environ.get('TREELINE_GEDTM30_TMP') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'download', 'tmp_gedtm30')
ARC = 3600
SOURCE = 'GEDTM30 v1.2 (OpenGeoHub, CC BY 4.0)'


def tile_path(lat, lon):
    """ The local 1° tile whose south-west corner is (lat, lon). """
    return os.path.join(DIR, f'{"N" if lat >= 0 else "S"}{abs(lat):02d}{"E" if lon >= 0 else "W"}{abs(lon):03d}.tif')


def _env():
    os.environ.setdefault('GDAL_HTTP_USERAGENT', 'treeline-client/0.1')
    os.environ.setdefault('GDAL_HTTP_MAX_RETRY', '5')          # /vsicurl's own retries, on 429 and 5xx
    os.environ.setdefault('GDAL_HTTP_RETRY_DELAY', '5')
    os.environ.setdefault('GDAL_HTTP_TIMEOUT', '120')
    os.environ.setdefault('GDAL_DISABLE_READDIR_ON_OPEN', 'EMPTY_DIR')


def whole(path):
    """ Whether a tile on disk reads back whole: the right size, every block decodable. """
    import rasterio
    try:
        with rasterio.open(path) as ds:
            if (ds.width, ds.height, ds.count) != (ARC, ARC, 1):
                return False
            ds.read(1)
        return True
    except Exception:          # noqa: BLE001 — a truncated or broken file
        return False


def read_window(r0, c0, h, w, tries=5, split=4):
    """
    Rows r0.. and cols c0.. of the COG, h × w cells, metres as float32 (no data NaN): retried with
    backoff (the network is shared and fails now and then), and if the whole window keeps failing,
    read as split × split smaller windows, each retried the same way.
    """
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    def once(r, c, hh, ww):
        with rasterio.open(URL) as ds:
            z = ds.read(1, window=Window(c, r, ww, hh), boundless=True, fill_value=ds.nodata)
            return np.where((z == ds.nodata) | ~np.isfinite(z) | (z > 9000) | (z < -1000), np.nan, z).astype(np.float32)

    def retried(r, c, hh, ww, n):
        for k in range(n):
            try:
                return once(r, c, hh, ww)
            except rasterio.errors.RasterioIOError as e:
                if k == n - 1:
                    raise
                wait = 10 * 2 ** k
                print(f'  read {r},{c} ({hh}×{ww}) failed ({e}); again in {wait} s', flush=True)
                time.sleep(wait)
    try:
        return retried(r0, c0, h, w, tries)
    except rasterio.errors.RasterioIOError:
        print(f'  window {r0},{c0} keeps failing: in {split * split} pieces', flush=True)
    z = np.empty((h, w), np.float32)
    edges_r = [r0 + h * i // split for i in range(split + 1)]
    edges_c = [c0 + w * j // split for j in range(split + 1)]
    for i in range(split):
        for j in range(split):
            a, b, c, d = edges_r[i], edges_r[i + 1], edges_c[j], edges_c[j + 1]
            z[a - r0:b - r0, c - c0:d - c0] = retried(a, c, b - a, d - c, tries + 3)
    return z


def fetch_tile(lat, lon):
    """ The tile whose south-west corner is (lat, lon), fetched unless it is on disk whole; written
    under a temporary name and renamed when complete. """
    _env()
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    out = tile_path(lat, lon)
    if os.path.exists(out) and whole(out):
        return out, 'had'
    with rasterio.open(URL) as ds:
        t = ds.transform
    # the cell centred at (lat + 1, lon): row (85 - lat - 1) * 3600 + 4, col (lon + 180) * 3600 + 4
    r0 = round((t.f - (lat + 1)) / -t.e - 0.5)
    c0 = round((lon - t.c) / t.a - 0.5)
    z = read_window(r0, c0, ARC, ARC)
    os.makedirs(DIR, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    tmp = os.path.join(TMP, f'{os.path.basename(out)}.{os.getpid()}.tif')
    with rasterio.open(tmp, 'w', driver='GTiff', width=ARC, height=ARC, count=1, dtype='float32', crs='EPSG:4326',
                       transform=from_origin(lon - 0.5 / ARC, lat + 1 + 0.5 / ARC, 1 / ARC, 1 / ARC), nodata=np.nan,
                       tiled=True, blockxsize=512, blockysize=512, compress='deflate', predictor=3) as o:
        o.write(z, 1)
    import compact
    compact.convert(tmp, out, 0.01, None, 1)       # every cell checked, written as out + '.part', renamed
    os.remove(tmp)
    return out, 'fetched'


def safe_fetch(lat, lon):
    """ fetch_tile, a failure reported rather than raised (the rest go on; a rerun retries it). """
    try:
        return fetch_tile(lat, lon)
    except Exception as e:     # noqa: BLE001
        return tile_path(lat, lon), f'FAILED ({e})'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('fetch')
    p.add_argument('--bbox', help='S,N,W,E in whole degrees')
    p.add_argument('--land', action='store_true', help='every 1° tile there is a Copernicus GLO-30 tile for (all land)')
    p.add_argument('--jobs', type=int, default=6)
    a = ap.parse_args()
    os.makedirs(DIR, exist_ok=True)
    if a.land:
        import glob
        import re
        todo = []
        for f in glob.glob(os.path.join(_path('copernicus', env='TREELINE_COPERNICUS'), '*_DEM.tif')):
            g = re.search(r'_([NS])(\d+)_00_([EW])(\d+)_00_DEM', f)
            if g:
                todo.append((int(g[2]) * (1 if g[1] == 'N' else -1), int(g[4]) * (1 if g[3] == 'E' else -1)))
        todo = sorted(set(todo))
    else:
        s, n, w, e = (int(v) for v in a.bbox.split(','))
        todo = [(la, lo) for la in range(s, n) for lo in range(w, e)]
    failed = 0
    with ProcessPoolExecutor(a.jobs) as ex:
        futures = {ex.submit(safe_fetch, la, lo): (la, lo) for la, lo in todo}
        for i, f in enumerate(as_completed(futures), 1):
            path, how = f.result()
            failed += how.startswith('FAILED')
            print(f'{i}/{len(todo)} {how} {os.path.basename(path)}', flush=True)
    print(f'{len(todo) - failed} of {len(todo)} tiles on disk; {failed} failed (run again to retry them)')
    raise SystemExit(1 if failed else 0)


if __name__ == '__main__':
    main()
