"""
Every summit of prominence >= P (default 30 m) in the lidar, catalogued or not: the
candidates for nearest higher neighbours. From the pooled 10 m grid, so each summit is at its
exact 1 m top and height; its prominence there is a lower bound (a pooled col can only be
too high), and a summit barely over P may be missed.

    uv run --group lidar python lidar/summits.py [--min-prominence 30] [--out FILE.npz]

The npz holds lat, lon, h (ellipsoidal, as the raster), prominence (pooled) per summit; the
highest point of the data is in with prominence inf.
"""
import argparse
import os
import time

import numpy as np
import rasterio
from pyproj import Transformer

from prominence import SOURCE, Grid, flood, pool


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--min-prominence', type=float, default=30.0)
    ap.add_argument('--work', default=os.path.expanduser('~/.cache/treeline/lidar'))
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    out = args.out or os.path.join(args.work, f'summits-p{args.min_prominence:g}.npz')

    top, at = pool(SOURCE, args.work, 10, 8, None)
    h, w = top.shape
    flat = np.ascontiguousarray(top).ravel()
    t = time.time()
    order = np.argsort(-flat, kind='stable').astype(np.int32)
    peaks, cols, _, _, _, _, _, rest = flood(flat, order, w, h, args.min_prominence)
    del order
    cells = np.concatenate([peaks, rest])
    prom = np.concatenate([flat[peaks] - flat[cols], np.full(len(rest), np.inf)])
    print(f'flooded in {time.time() - t:.0f}s: {len(cells)} summits with P >= {args.min_prominence:g}', flush=True)

    with rasterio.open(SOURCE) as d:
        grid = Grid(np.zeros((1, 1), np.float32), at, 10, 0, (0, 0), d.transform)
        grid.h, grid.w, grid.W = h, w, w
        grid.filled = np.zeros((h, w), np.bool_)
        to_ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
        rc = np.array([grid.pixel(i) for i in cells])
        e, n = d.transform * (rc[:, 1] + 0.5, rc[:, 0] + 0.5)
        lon, lat = to_ll.transform(e, n)
    np.savez(out, lat=np.asarray(lat), lon=np.asarray(lon), h=flat[cells].astype(np.float64), prominence=prom)
    print(f'-> {out}')


if __name__ == '__main__':
    main()
