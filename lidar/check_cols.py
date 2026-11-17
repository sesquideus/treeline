"""
Is a key col the database has from the lidar really the key col, on the 1 m data? The
pooled 10 m search can take one col for another where two routes to higher ground have cols
within a couple of metres (Veľké Stráne): the height stored is then right for the col, but
the col is the wrong one.

For each summit whose key col came from the lidar, the highest route at 1 m between its top
and its parent's top, in a window around both: no route to the parent is higher than the key
col, so a route that is higher proves the stored col wrong. One that is not is consistent
(though the window may have cut a longer route short).

    LIDAR_DB=db.json ... ; uv run --group lidar python lidar/check_cols.py DB.json OUT.json [--max-km 10]
"""
import argparse
import json
import math

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.windows import Window

from prominence import SOURCE, join_height


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('db')
    ap.add_argument('out')
    ap.add_argument('--max-km', type=float, default=10)
    ap.add_argument('--margin', type=int, default=500, help='metres of window around the pair')
    ap.add_argument('--tolerance', type=float, default=0.5)
    args = ap.parse_args()
    summits = json.load(open(args.db))['summits']
    by_pk = {s['pk']: s for s in summits}
    d = rasterio.open(SOURCE)
    tm = Transformer.from_crs(4326, d.crs, always_xy=True)
    ll = Transformer.from_crs(d.crs, 4326, always_xy=True)
    bpv = Transformer.from_crs('EPSG:4937', 'EPSG:4258+8357', always_xy=True)

    def km(a, b):
        return math.hypot((a[1] - b[1]) * 111320 * math.cos(math.radians(a[0])), (a[0] - b[0]) * 110574) / 1000

    todo = [s for s in summits if s['key_col'] and s['key_col'].get('source') == 'DMR 5.0'
            and s['parent'] and s['parent']['pk'] in by_pk]
    out, wrong = [], 0
    for k, s in enumerate(todo, 1):
        p = by_pk[s['parent']['pk']]
        a, b = (s['point']['lat'], s['point']['lon']), (p['point']['lat'], p['point']['lon'])
        row = dict(pk=s['pk'], name=s['point']['name'], col=s['key_col']['name'], col_pk=s['key_col']['pk'],
                   stored=s['key_col']['alt'], parent=p['point']['name'], km=round(km(a, b), 1))
        if row['km'] > args.max_km:
            out.append(dict(row, verdict='too far'))
            continue
        ra, ca = d.index(*tm.transform(a[1], a[0]))
        rb, cb = d.index(*tm.transform(b[1], b[0]))
        r0, c0 = min(ra, rb) - args.margin, min(ca, cb) - args.margin
        h, w = max(ra, rb) + args.margin - r0, max(ca, cb) + args.margin - c0
        z = d.read(1, window=Window(c0, r0, w, h), boundless=True, fill_value=d.nodata).astype(np.float32)
        z[~np.isfinite(z) | (z > 1e30)] = -np.inf
        if np.isinf(z).mean() > 0.05:
            out.append(dict(row, verdict='window leaves the lidar'))
            continue

        def top(r, c):      # the highest pixel within 5 m: the summit's own top
            sub = z[r - 5:r + 6, c - 5:c + 6]
            i = np.unravel_index(np.argmax(sub), sub.shape)
            return (r - 5 + i[0]) * w + (c - 5 + i[1])
        height, at = join_height(z, w, h, top(ra - r0, ca - c0), top(rb - r0, cb - c0))
        jr, jc = divmod(int(at), w)
        e, n = d.transform * (c0 + jc + 0.5, r0 + jr + 0.5)
        lon, lat = ll.transform(e, n)
        route = float(bpv.transform(lon, lat, float(height))[2])
        verdict = 'wrong col' if route > row['stored'] + args.tolerance else 'consistent'
        wrong += verdict == 'wrong col'
        out.append(dict(row, route=round(route, 2), route_at=(round(lat, 6), round(lon, 6)), verdict=verdict))
        if verdict == 'wrong col' or k % 100 == 0:
            print(f'{k}/{len(todo)} {row["name"]}: stored {row["stored"]}, route {route:.2f} -> {verdict}', flush=True)
    json.dump(out, open(args.out, 'w'), ensure_ascii=False, indent=1)
    counts = {}
    for r in out:
        counts[r['verdict']] = counts.get(r['verdict'], 0) + 1
    print(counts)


if __name__ == '__main__':
    main()
