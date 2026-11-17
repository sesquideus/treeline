"""
Altitudes and positions from the lidar, where the case is clear, as a changeset of point
corrections for `manage.py apply_lidar_changeset`.

    uv run --group lidar python lidar/altitudes.py comparison.json DB.json OUT.json

A summit and its key col are corrected together, and only when all of this holds:

- the lidar alone decided them: no Copernicus anywhere in the result;
- both are inside Slovakia and more than --border metres from its border;
- the summit moves at most --summit-move metres, the key col at most --col-move (the same
  col, then, not another one);
- no rock: no slope steeper than --rock degrees within 10 m of either point on the 1 m model;
- the change goes the plausible way: a summit the lidar has more than 2 m lower looks shaved
  (a crag), a col it has more than 2 m higher looks bridged (a notch); neither is touched;
- neither point's source is one better than the lidar (PROTECTED), and its height is in Bpv.

Each point that passes is corrected on its own; then any correction that would break an
invariant (key col below its summit, parent higher and more prominent) is dropped again,
until none does.
"""
import argparse
import json
import math
import os

import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.windows import Window
from shapely import wkt
from shapely.geometry import Point

from prominence import SOURCE

PROTECTED = {'Państwowy zasób geodezyjny i kartograficzny', 'Výskum a ochrana Malej Fatry 2017'}
BORDER = os.path.expanduser('~/.cache/treeline/lidar/osm/slovakia.wkt')


def dist(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * 111320 * k, (a[0] - b[0]) * 110574)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('comparison')
    ap.add_argument('db')
    ap.add_argument('out')
    ap.add_argument('--border', type=float, default=1000)
    ap.add_argument('--summit-move', type=float, default=30)
    ap.add_argument('--col-move', type=float, default=100)
    ap.add_argument('--rock', type=float, default=50, help='degrees')
    ap.add_argument('--loose', action='store_true',
                    help='the second pass: summits move up to 150 m and key cols up to 500 m; rock only '
                         'blocks the suspicious direction (a summit lowered, a col raised); parents follow '
                         'the lidar walk where it leads to a DB summit')
    args = ap.parse_args()
    if args.loose:
        args.summit_move, args.col_move = 150, 500
    report, db = json.load(open(args.comparison)), json.load(open(args.db))
    by_pk = {s['pk']: s for s in db['summits']}

    tm = Transformer.from_crs(4326, 3046, always_xy=True)
    slovakia = shapely.transform(wkt.loads(open(BORDER).read()),
                                 lambda c: np.column_stack(tm.transform(c[:, 0], c[:, 1])))
    line = slovakia.boundary

    def inland(ll):
        here = Point(*tm.transform(ll[1], ll[0]))
        return slovakia.contains(here) and line.distance(here) > args.border

    d = rasterio.open(SOURCE)

    def rocky(ll):
        r, c = d.index(*tm.transform(ll[1], ll[0]))
        z = d.read(1, window=Window(c - 11, r - 11, 23, 23)).astype(np.float64)
        if (z > 1e30).any():
            return True
        gy, gx = np.gradient(z)
        steep = np.degrees(np.arctan(np.hypot(gx, gy)))[1:-1, 1:-1]
        return steep.max() > args.rock

    why = {}
    changes = {}          # point pk -> correction
    for key in ('good', 'amiss'):
        for e in report[key]:
            x, s = e['lidar'], by_pk[e['db']['pk']]
            p, kc = s['point'], s['key_col']
            flags = ' '.join(x['flags'])

            def no(reason):
                why[reason] = why.get(reason, 0) + 1
            if 'Copernicus' in flags or 'beyond' in flags or x.get('prom_alone') != x['prom'] or x['col'] is None:
                no('Copernicus or no lidar key col'); continue
            if kc is None:
                no('no key col in the DB'); continue
            if not (inland(x['ll']) and inland(x['col'])):
                no(f'within {args.border:g} m of the border'); continue
            # the summit
            moved = dist((p['lat'], p['lon']), x['ll'])
            if p['datum'] not in ('Bpv', None) or p.get('source') in PROTECTED:
                no('summit: protected source or not Bpv')
            elif moved > args.summit_move:
                no(f'summit moves more than {args.summit_move:g} m')
            elif x['bpv'] - p['alt'] < -2 and (not args.loose or rocky(x['ll'])):
                no('summit: lidar more than 2 m lower (a crag?)')
            elif rocky(x['ll']) and not args.loose:
                no('summit: rock within 10 m')
            elif abs(x['bpv'] - p['alt']) >= 0.05 or moved >= 1:
                changes[p['pk']] = dict(point=p['pk'], name=p['name'], lat=x['ll'][0], lon=x['ll'][1],
                                        altitude=x['bpv'], datum='Bpv', was=p['alt'], moved=round(moved))
            # its key col
            moved = dist((kc['lat'], kc['lon']), x['col'])
            if kc['datum'] not in ('Bpv', None) or kc.get('source') in PROTECTED:
                no('col: protected source or not Bpv')
            elif moved > args.col_move:
                no(f'col moves more than {args.col_move:g} m')
            elif x['col_bpv'] - kc['alt'] > 2 and (not args.loose or rocky(x['col'])):
                # Only rock makes a notch the ground model can bridge; elsewhere the lidar stands.
                no('col: lidar more than 2 m higher (a notch?)')
            elif rocky(x['col']) and not args.loose:
                no('col: rock within 10 m')
            elif abs(x['col_bpv'] - kc['alt']) >= 0.05 or moved >= 1:
                changes[kc['pk']] = dict(point=kc['pk'], name=kc['name'], lat=x['col'][0], lon=x['col'][1],
                                         altitude=x['col_bpv'], datum='Bpv', was=kc['alt'], moved=round(moved))

    relinks = []
    if args.loose:
        for key in ('good', 'amiss'):
            for e in report[key]:
                x, s = e['lidar'], by_pk[e['db']['pk']]
                if not x.get('parent') or not s['parent'] or not s['key_col']:
                    continue
                if 'Copernicus' in ' '.join(x['flags']) or x.get('prom_alone') != x['prom']:
                    continue
                if not (inland(x['ll']) and inland(x['col'])):
                    continue
                near = min(db['summits'], key=lambda t: dist((t['point']['lat'], t['point']['lon']), x['parent']))
                if dist((near['point']['lat'], near['point']['lon']), x['parent']) > 150 or near['pk'] == s['parent']['pk']:
                    continue
                if not inland(x['parent']):
                    continue
                relinks.append(dict(summit=s['pk'], summit_name=s['point']['name'],
                                    key_col=dict(point=s['key_col']['pk'], name=s['key_col']['name']),
                                    parent=dict(pk=near['pk'], name=near['point']['name']),
                                    was=s['parent']['name']))

    # Drop what would break an invariant, until nothing does.
    dropped = []
    while True:
        alt = lambda pt: changes[pt['pk']]['altitude'] if pt and pt['pk'] in changes else (pt['alt'] if pt else None)
        prom = {}
        for s in db['summits']:
            if s['key_col']:
                prom[s['pk']] = alt(s['point']) - alt(s['key_col'])
        bad = set()
        for s in db['summits']:
            if s['key_col'] and alt(s['key_col']) >= alt(s['point']):
                bad |= {s['point']['pk'], s['key_col']['pk']}
            par = s['parent'] and by_pk.get(s['parent']['pk'])
            if par and s['pk'] in prom and par['pk'] in prom:
                if alt(par['point']) <= alt(s['point']) or prom[par['pk']] <= prom[s['pk']]:
                    bad |= {s['point']['pk'], par['point']['pk']} | {s['key_col']['pk'], par['key_col']['pk']}
        bad &= set(changes)
        if not bad:
            break
        for pk in bad:
            dropped.append(changes.pop(pk))

    json.dump(dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
                   points=[{k: v for k, v in c.items() if k not in ('was', 'moved')} for c in changes.values()],
                   relinks=[{k: v for k, v in r.items() if k != 'was'} for r in relinks]),
              open(args.out, 'w'), ensure_ascii=False, indent=1)
    deltas = np.array([c['altitude'] - c['was'] for c in changes.values()])
    print(f'{len(changes)} points corrected, {len(dropped)} dropped for an invariant, {len(relinks)} parents relinked')
    if len(deltas):
        print(f'  height change: median {np.median(deltas):+.2f} m, 5–95 % {np.percentile(deltas, 5):+.2f} … '
              f'{np.percentile(deltas, 95):+.2f} m, largest {deltas[np.argmax(abs(deltas))]:+.2f} m')
    for reason, n in sorted(why.items(), key=lambda t: -t[1]):
        print(f'  not touched: {n:4} × {reason}')
    json.dump(dict(changes=list(changes.values()), dropped=dropped, relinks=relinks), open(args.out + '.detail', 'w'),
              ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
