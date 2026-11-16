"""
For DB summits away from their lidar top: does the DB point stand on a top of its own?

    uv run --group lidar python lidar/tops.py comparison.json DB.json OUT.json

The highest 1 m pixel within 20 m of the DB point is its own top. Flooding a window around
both from the top down, the height where that top's ground first joins the lidar top's is
the saddle between them; top minus saddle is the drop. A DB point on the lidar top's slope
climbs straight up to it, and its drop is nil.
"""
import json
import math
import sys

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from prominence import SOURCE, join_height


def main():
    report, db, out = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3]
    by_pk = {s['pk']: s for s in db['summits']}
    d = rasterio.open(SOURCE)
    to_tm = Transformer.from_crs(4326, 3046, always_xy=True)
    to_ll = Transformer.from_crs(3046, 4326, always_xy=True)
    bpv = Transformer.from_crs('EPSG:4937', 'EPSG:4258+8357', always_xy=True)
    found = []
    for key in ('wrong', 'amiss'):
        for e in report[key]:
            if not any(t.startswith('summit') for t in e['wrong'] + e['issues']):
                continue
            s = by_pk[e['db']['pk']]
            x = e['lidar']
            pa = d.index(*to_tm.transform(s['point']['lon'], s['point']['lat']))
            pb = d.index(*to_tm.transform(x['ll'][1], x['ll'][0]))
            r0, c0 = min(pa[0], pb[0]) - 300, min(pa[1], pb[1]) - 300
            h, w = max(pa[0], pb[0]) + 300 - r0, max(pa[1], pb[1]) + 300 - c0
            z = d.read(1, window=Window(c0, r0, w, h), boundless=True, fill_value=d.nodata).astype(np.float32)
            z[~np.isfinite(z) | (z > 1e30)] = -np.inf
            ar, ac = pa[0] - r0, pa[1] - c0
            sub = z[ar - 20:ar + 21, ac - 20:ac + 21]
            i = np.unravel_index(np.argmax(sub), sub.shape)
            tr, tc = ar - 20 + i[0], ac - 20 + i[1]
            br, bc = pb[0] - r0, pb[1] - c0
            sub = z[br - 3:br + 4, bc - 3:bc + 4]          # the lidar top, to the pixel
            j = np.unravel_index(np.argmax(sub), sub.shape)
            br, bc = br - 3 + j[0], bc - 3 + j[1]
            saddle, p = join_height(z, w, h, tr * w + tc, br * w + bc)
            sr, sc = divmod(int(p), w)

            def place(r, c, height):
                east, north = d.transform * (c0 + c + 0.5, r0 + r + 0.5)
                lon, lat = to_ll.transform(east, north)
                return dict(lat=round(lat, 6), lon=round(lon, 6),
                            bpv=round(float(bpv.transform(lon, lat, float(height))[2]), 2))
            own, lidar_top = place(tr, tc, z[tr, tc]), place(br, bc, z[br, bc])
            found.append(dict(
                pk=s['pk'], name=s['point']['name'], db=dict(lat=s['point']['lat'], lon=s['point']['lon'], alt=s['point']['alt']),
                own=own, top=lidar_top, saddle=place(sr, sc, saddle), drop=round(float(z[tr, tc] - saddle), 2),
                apart=round(math.hypot(tr - br, tc - bc)), lidar=x, flags=x['flags'],
                messages=e['wrong'] + e['issues']))
            print(f'{s["point"]["name"] or "unnamed":32} {found[-1]["apart"]:5} m apart, own top {own["bpv"]:7.1f} '
                  f'vs {lidar_top["bpv"]:7.1f}, drop {found[-1]["drop"]:6.2f}', flush=True)
    json.dump(found, open(out, 'w'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
