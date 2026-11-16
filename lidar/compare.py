"""
The lidar P100 list against the database, for the summits the lidar covers.

    uv run --group lidar python lidar/compare.py DB.json OUTDIR > comparison.json

DB.json is a dump of summits (point, prominence, key col, parent) and of named points that
are not summits; OUTDIR holds `prominence.py`'s and `parents.py`'s output.
"""
import csv
import json
import math
import os
import sys

import numpy as np
import rasterio
from pyproj import Transformer

from prominence import SOURCE

POS_TOL = 15        # m, summit position
ALT_TOL = 2.0       # m, summit and col altitude
PROM_TOL = 5.0      # m
COL_POS_TOL = 50    # m
PARENT_TOL = 150    # m
MATCH_RADIUS = 150  # m: the same summit
NEAR_RADIUS = 500   # m: probably the same summit, badly placed


def dist(a, b):
    """Metres between two (lat, lon), equirectangular: fine at these distances."""
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * 111320 * k, (a[0] - b[0]) * 110574)


def norm(name):
    """A name without its disambiguating qualifier: 'Volovec (Západné Tatry)' is 'volovec'."""
    import re
    name = re.sub(r'\([^)]*\)', '', name or '').replace('"', '').replace('„', '').replace('“', '')
    return ' '.join(name.lower().split())


def variants(name):
    """The name without its qualifier, and with it inline: '(južný vrchol)' = '- južný vrchol'."""
    import re
    inline = ' '.join(re.sub(r'[()\-–,"„“]', ' ', name or '').lower().split())
    return {norm(name), inline}


def fold(name):
    import unicodedata
    # Accents, case, hyphens and spaces: "Jégvölgyi csúcs" is "Jég-völgyi-csúcs" spelled otherwise.
    return ''.join(c for c in unicodedata.normalize('NFD', norm(name))
                   if unicodedata.category(c) != 'Mn' and c.isalnum())


class Osm:
    """OSM peaks and saddles (what Freemap draws), nearest-first lookups."""

    def __init__(self, path):
        self.items = json.load(open(path))
        self.lat = np.array([o['lat'] for o in self.items])
        self.lon = np.array([o['lon'] for o in self.items])
        self.kind = np.array([o['kind'] for o in self.items])

    def nearest(self, ll, kinds, radius):
        k = math.cos(math.radians(ll[0]))
        d = np.hypot((self.lon - ll[1]) * 111320 * k, (self.lat - ll[0]) * 110574)
        d[~np.isin(self.kind, kinds)] = np.inf
        named = np.array([bool(o['name']) for o in self.items])
        for want in (named, ~named):          # a named one in range beats a nearer nameless one
            dd = np.where(want, d, np.inf)
            i = int(np.argmin(dd))
            if dd[i] <= radius:
                o = self.items[i]
                other = o.get('other_names', {})
                split = lambda v: [part.strip() for part in v.split(';') if part.strip()]
                langs = {k[5:]: split(v) for k, v in other.items() if k.startswith('name:')}
                names = ([o['name']] if o['name'] else []) + [n for v in other.values() for n in split(v)]
                return dict(lat=o['lat'], lon=o['lon'], name=o['name'], names=list(dict.fromkeys(names)),
                            langs=langs, ele=o['ele'], dist=round(float(dd[i])), kind=o['kind'], id=o['id'])
        return None


def name_check(db_names, osm, col=False):
    """
    'same', 'spelling', 'differs', 'osm only' (the DB has none), 'db only' (OSM has none).
    A col's name is compared without the word "sedlo": "Obšiar" is "Sedlo Obšiar".
    """
    db_names = [n for n in db_names if n and not n.startswith('(')]
    if not osm or not osm['names']:
        return 'db only' if db_names else None
    if not db_names:
        return 'osm only'
    osm_names = [part.strip() for n in osm['names'] for part in n.split(' / ')]

    def forms(n):
        out = variants(n)
        if col:
            out = {' '.join(w for w in v.split() if w not in ('sedlo', 'sedle')) for v in out}
        return out
    a, b = {v for n in db_names for v in forms(n)}, {v for n in osm_names for v in forms(n)}
    if a & b:
        return 'same'
    if {fold(v) for v in a} & {fold(v) for v in b}:
        return 'spelling'
    return 'differs'


def lang_check(db_langs, osm, col=False):
    """
    Per `name:xx` tag on the OSM point: 'missing' when the DB has no name in that language,
    'differs' / 'spelling' when it has others. Historical DB names count as known.
    """
    if not osm or not osm.get('langs'):
        return []
    have = {}
    for n in db_langs:
        have.setdefault(n['lang'], []).append(n['name'])
    found = []
    for lang, names in sorted(osm['langs'].items()):
        if lang not in have:
            found.append(dict(lang=lang, osm=names, db=[], status='missing'))
            continue
        status = name_check(have[lang], dict(names=names), col)
        if status in ('differs', 'spelling'):
            found.append(dict(lang=lang, osm=names, db=have[lang], status=status))
    return found


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    db = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    lidar = list(csv.DictReader(open(os.path.join(out, 'p100.csv'))))
    rejected = list(csv.DictReader(open(os.path.join(out, 'p100-rejected.csv'))))
    parents = {(r['lat'], r['lon']): r for r in csv.DictReader(open(os.path.join(out, 'p100-parents.csv')))}

    # What the lidar covers: a DB summit outside it is not this comparison's business.
    top = np.load(os.path.expanduser('~/.cache/treeline/lidar/pooled10.npy'), mmap_mode='r')
    with rasterio.open(SOURCE) as d:
        to_tm = Transformer.from_crs(4326, d.crs, always_xy=True)
        inv = ~d.transform

        def covered(lat, lon):
            c, r = inv * to_tm.transform(lon, lat)
            r, c = int(r) // 10, int(c) // 10
            return 0 <= r < top.shape[0] and 0 <= c < top.shape[1] and np.isfinite(top[r, c])

    egm = Transformer.from_crs('EPSG:4937', 'EPSG:4326+3855', always_xy=True)
    summits = [s for s in db['summits'] if covered(s['point']['lat'], s['point']['lon'])]
    for s in summits:
        p = s['point']
        s['ll'] = (p['lat'], p['lon'])

    L = []
    for r in lidar:
        ll = (float(r['lat']), float(r['lon']))
        par = parents.get((r['lat'], r['lon']))
        prom = num(r['prominence'])
        # P100 only with Copernicus lowered 25 m: that lowers the peaks across the border as
        # well as its cols, so a higher neighbour there (Paportna over Jarabá skala) drops
        # out. Not to be trusted as a P100.
        doubtful = r['prominence'] != '' and (prom is None or prom < 100)    # '' is the top
        L.append(dict(
            ll=ll, egm=num(r['alt_egm2008']), bpv=num(r['alt_bpv']), doubtful=doubtful,
            prom_raw=r['prominence'],
            prom=num(r['prominence_canopy_discounted']) if doubtful else prom,
            prom_alone=num(r['prominence_lidar_alone']),
            col=(num(r['col_lat']), num(r['col_lon'])) if r['col_lat'] else None,
            col_egm=num(r['col_egm2008']), col_bpv=num(r['col_bpv']),
            parent=(num(par['parent_lat']), num(par['parent_lon'])) if par and par['parent_lat'] else None,
            flags=[f for f in r['flags'].split('; ') if f], rank=int(r['rank'])))

    def nearest(ll, pool, key):
        best, bd = None, 1e18
        for x in pool:
            dd = dist(ll, key(x))
            if dd < bd:
                best, bd = x, dd
        return best, bd

    # Which side of the border each lidar top is on: the DMR reaches a little past it.
    border_file = os.path.expanduser('~/.cache/treeline/lidar/osm/slovakia.wkt')
    if os.path.exists(border_file):
        import shapely
        from shapely import wkt
        tm = Transformer.from_crs(4326, 3046, always_xy=True)
        slovakia = shapely.transform(wkt.loads(open(border_file).read()),
                                     lambda c: np.column_stack(tm.transform(c[:, 0], c[:, 1])))
        line = slovakia.boundary
        from shapely.geometry import Point as P
        for x in L:
            here = P(*tm.transform(x['ll'][1], x['ll'][0]))
            x['border_m'] = round(line.distance(here))
            x['inside'] = slovakia.contains(here)

    pts = db['points']
    report = dict(missed=[], wrong=[], amiss=[], good=[], db_only=[], doubtful=[], abroad=[])
    matched_db = set()
    for x in L:
        s, dd = nearest(x['ll'], summits, lambda s: s['ll'])
        # OSM's border is good to a few tens of metres: Babia hora's top lands 27 m into Poland.
        if 'inside' in x and x['border_m'] <= 30:
            x['flags'] = x['flags'] + [f'on the border ({x["border_m"]} m from it)']
        elif 'inside' in x and not x['inside']:
            x['flags'] = x['flags'] + [f'outside Slovakia, {x["border_m"]} m beyond the border']
        if x['doubtful']:
            hit = s if s is not None and dd <= NEAR_RADIUS else None
            if hit is not None:
                matched_db.add(hit['pk'])
            report['doubtful'].append(dict(
                lidar=x, db=hit and dict(pk=hit['pk'], name=hit['point']['name'], alt=hit['point']['alt'],
                                         datum=hit['point']['datum'], prominence=hit['prominence'],
                                         parent=hit['parent']['name'] if hit['parent'] else None),
                dist=dd))
            continue
        if s is None or dd > NEAR_RADIUS:
            hint, hd = nearest(x['ll'], pts, lambda p: (p['lat'], p['lon']))
            near, nd = (s, dd)
            report['missed'].append(dict(lidar=x, point=hint if hd < 300 else None, point_dist=hd,
                                         nearest=near and dict(name=near['point']['name'], pk=near['pk']),
                                         nearest_dist=nd))
            continue
        matched_db.add(s['pk'])
        issues, wrong = [], []
        p = s['point']
        if dd > MATCH_RADIUS:
            wrong.append(f'summit {dd:.0f} m from the lidar top')
        elif dd > POS_TOL:
            issues.append(f'summit {dd:.0f} m from the lidar top')
        # altitude: EGM2008 on both sides where the DB has it
        db_alt = p['egm'] if p['egm'] is not None else p['alt']
        li_alt = x['egm'] if p['egm'] is not None else (x['bpv'] or x['egm'])
        da = db_alt - li_alt if db_alt is not None and li_alt is not None else None
        if da is not None and abs(da) > ALT_TOL:
            # The ground model sometimes shaves off a rocky top: DB higher is not always DB wrong.
            note = ' (DB higher: a rock top the lidar ground model shaved off?)' if da > 0 else ''
            (wrong if abs(da) > 10 else issues).append(f'altitude {da:+.1f} m against the lidar{note}')
        dp = (s['prominence'] - x['prom']) if s['prominence'] is not None and x['prom'] is not None else None
        if s['prominence'] is None:
            wrong.append('no prominence in the DB')
        elif s['prominence'] < 100 <= x['prom']:
            wrong.append(f'DB prominence {s["prominence"]:.1f} m, lidar {x["prom"]:.1f} m: a P100 the DB does not count')
        elif dp is not None and abs(dp) > PROM_TOL:
            (wrong if abs(dp) > 20 else issues).append(f'prominence {dp:+.1f} m against the lidar')
        kc = s['key_col']
        if x['col'] is not None:
            if kc is None:
                if not s['island']:
                    wrong.append('no key col in the DB')
            else:
                cd = dist((kc['lat'], kc['lon']), x['col'])
                kalt = kc['egm'] if kc['egm'] is not None else kc['alt']
                lalt = x['col_egm'] if kc['egm'] is not None else (x['col_bpv'] or x['col_egm'])
                same_height = kalt is not None and lalt is not None and abs(kalt - lalt) <= ALT_TOL
                if cd > 500 and same_height:
                    issues.append(f'key col {cd / 1000:.1f} km from the lidar one, at the same height '
                                  f'({kalt - lalt:+.1f} m): a broad saddle, or two equal ones')
                elif cd > 500:
                    wrong.append(f'a different key col: the DB one is {cd / 1000:.1f} km from the lidar one')
                elif cd > COL_POS_TOL:
                    issues.append(f'key col {cd:.0f} m from the lidar saddle')
                if kalt is not None and lalt is not None and abs(kalt - lalt) > ALT_TOL and cd <= 500:
                    note = (' (the lidar higher: a narrow notch its ground model bridges?)'
                            if kalt < lalt else '')
                    (wrong if abs(kalt - lalt) > 10 else issues).append(
                        f'key col altitude {kalt - lalt:+.1f} m against the lidar{note}')
        if x['parent'] is not None:
            par = s['parent']
            if par is None:
                issues.append('no prominence parent in the DB')
            elif not covered(par['lat'], par['lon']):
                pass        # across the border: the lidar-only walk cannot reach it
            else:
                pd = dist((par['lat'], par['lon']), x['parent'])
                if pd > PARENT_TOL:
                    lp, lpd = nearest(x['parent'], summits, lambda t: t['ll'])
                    named = (f'{lp["point"]["name"]}' if lp and lpd < MATCH_RADIUS
                             else f'an uncatalogued peak at {x["parent"][0]:.5f}, {x["parent"][1]:.5f}')
                    issues.append(f'parent: DB has {par["name"]}, the lidar walk gives {named}')
        entry = dict(lidar=x, db=dict(pk=s['pk'], name=p['name'], alt=p['alt'], datum=p['datum'],
                                       prominence=s['prominence'], key_col_pk=s['key_col_pk'],
                                       key_col_name=kc['name'] if kc else None,
                                       parent=s['parent']['name'] if s['parent'] else None),
                     dist=dd, d_alt=da, d_prom=dp, issues=issues, wrong=wrong)
        report['wrong' if wrong else 'amiss' if issues else 'good'].append(entry)

    # DB summits the DB counts as P100 that the lidar list does not have
    rej = {(r['lat'], r['lon']): r for r in rejected}
    for s in summits:
        if s['pk'] in matched_db or s['prominence'] is None or s['prominence'] < 100:
            continue
        r, rd = nearest(s['ll'], [dict(ll=(float(k[0]), float(k[1])), row=v) for k, v in rej.items()],
                        lambda t: t['ll'])
        par, pd = nearest(s['ll'], [dict(ll=(float(k[0]), float(k[1])), row=v) for k, v in parents.items()],
                          lambda t: t['ll'])
        report['db_only'].append(dict(
            db=dict(pk=s['pk'], name=s['point']['name'], alt=s['point']['alt'], prominence=s['prominence']),
            rejected=r['row'] if r and rd < MATCH_RADIUS else None,
            pooled_prominence=num(par['row']['pooled_prominence']) if par and pd < MATCH_RADIUS else None))
    # A summit the DB has far from where it is: same height and prominence, wrong place.
    loose = [s for s in summits if s['pk'] not in matched_db]
    for e in list(report['missed']):
        x = e['lidar']
        for s in loose:
            alt = s['point']['alt']
            li = x['bpv'] or x['egm']
            if (dist(x['ll'], s['ll']) < 3000 and alt is not None and abs(alt - li) <= 5
                    and s['prominence'] is not None and x['prom'] is not None
                    and abs(s['prominence'] - x['prom']) <= 10):
                dd = dist(x['ll'], s['ll'])
                report['missed'].remove(e)
                report['db_only'] = [o for o in report['db_only'] if o['db']['pk'] != s['pk']]
                matched_db.add(s['pk'])
                loose.remove(s)
                report['wrong'].append(dict(
                    lidar=x, db=dict(pk=s['pk'], name=s['point']['name'], alt=alt, datum=s['point']['datum'],
                                     prominence=s['prominence'], key_col_pk=s['key_col_pk'],
                                     key_col_name=s['key_col']['name'] if s['key_col'] else None,
                                     parent=s['parent']['name'] if s['parent'] else None),
                    dist=dd, d_alt=alt - li, d_prom=s['prominence'] - x['prom'], issues=[],
                    wrong=[f'summit {dd:.0f} m from the lidar top, where height and prominence agree '
                           f'({alt - li:+.1f} m, {s["prominence"] - x["prom"]:+.1f} m): the DB position is off']))
                break
    if len(sys.argv) > 3:
        osm = Osm(sys.argv[3])
        by_pk = {s['pk']: s for s in summits}
        for key in ('missed', 'wrong', 'amiss', 'good', 'doubtful', 'abroad'):
            for e in report[key]:
                x = e['lidar']
                x['osm'] = (osm.nearest(x['ll'], ['peak', 'volcano', 'hill'], 150)
                            or osm.nearest(x['ll'], ['rock', 'stone'], 50))
                x['osm_col'] = osm.nearest(x['col'], ['saddle'], 150) if x['col'] and x['col'][0] else None
                d = e.get('db')
                if d:
                    s = by_pk[d['pk']]
                    e['name'] = name_check([s['point']['name']] + s['point'].get('names', []), x['osm'])
                    e['langs'] = lang_check(s['point'].get('langs', []), x['osm'])
                    if e['name'] == 'db only':
                        # Freemap may have it a little further off: say where.
                        e['osm_far'] = osm.nearest(x['ll'], ['peak', 'volcano', 'hill', 'rock'], 1000)
                    kc = s['key_col']
                    e['col_name'] = (name_check([kc['name']] + kc.get('names', []), x['osm_col'], col=True)
                                     if kc and dist((kc['lat'], kc['lon']), x['col'] or (0, 0)) < 500 else None)
                    d['col_name'] = kc['name'] if kc else None
                    e['col_langs'] = (lang_check(kc.get('langs', []), x['osm_col'], col=True)
                                      if kc and dist((kc['lat'], kc['lon']), x['col'] or (0, 0)) < 500 else [])
    report['coverage'] = dict(db_summits_covered=len(summits), lidar=len(L))
    json.dump(report, sys.stdout, default=str)


if __name__ == '__main__':
    main()
