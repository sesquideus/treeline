"""
A `joint.py` run's tops against the database: changesets for what only improves, and a review list
for what needs a person. `catchments_db.py` for a joint run over several countries.

    RUN=RUN.json OUT=DIR COUNTRIES=sk [NEW=cz,at] [SKIP=sk] [BORDER_ONLY=sk] [P_MIN=30] [BIG=5] [NEAR_NAMED=300] \
        uv run python manage.py shell < lidar/joint_changesets.py

The database's summits in COUNTRIES are checked; new summits of P >= P_MIN are added where the run's
top stands on the data of a country in NEW (default: COUNTRIES). Writes DIR/changeset-summits.json
(new summits: apply first), DIR/changeset-fixes.json (heights, positions, key cols, parents) and
DIR/review.json. Run it again after applying until it finds nothing.

Heights: the run's are EGM2008. A point takes its country's datum, the defaults the user chose:
Bpv in Slovakia and Czechia (converted through the national grids, never a ballpark), EGM2008
elsewhere (Austria: the number as measured, its datum said). A country is the agency whose data the
point stands on; a settled top's ground lies inside that data, so the top lies inside the country.

Never degrade (as in `catchments_db.py`, plus):
- A summit is matched to a top at its height (EGM2008, within 1.5 m; 3 m where its datum is
  unknown) within 30 m, else within 600 m (it is moved onto it: list coordinates can be that far off), else to the nearest top within
  30 m. A height off by more than BIG is for review, and its key col and parent stay: the point may
  be on the wrong top. Two database summits on one top are for review, both left as they are.
- Key cols and parents change only on settled tops (ground inside the data) and where they came
  from the lidar, Freemap measurements, a guess, Wikipedia or nowhere; where they came from a
  computed list (Peaklist, FABDEM), also when the prominence moves by no more than LIST_DP.
  A parent changes only where the run proves it (`parent_proven`: no data beyond can change it);
  otherwise the database's stays.
- Points measured on DMR 5.0's own grid keep their heights (the joint grid is resampled), unless a
  relink moves them; a key col within FLAT of the run's height is the same saddle.
- BORDER_ONLY=sk: what the data beyond the border changes, nothing else. A Slovak summit is
  relinked only where its key col or parent, the database's or the run's, lies outside Slovakia;
  points are re-measured only as those relinks move them. Slovakia was measured on DMR 5.0's own
  grid already; the rest of the differences (resampling, mostly centimetres) is listed, not applied.
- New summits: settled, P >= P_MIN, no needles (key col within 10 m), none within 300 m of a named
  database summit, none within 1.5 km of a database summit at its height (within 3 m: the same peak,
  misplaced); never as a parent either; OSM names where an extract is at hand (Slovakia), else unnamed.
"""
import collections
import json
import math
import os
import re

from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D as Dm
from pyproj import Transformer
from pyproj.exceptions import ProjError

from mountains.models import Col, NamedPoint, Summit

D = os.environ['OUT']
os.makedirs(D, exist_ok=True)
COUNTRIES = os.environ['COUNTRIES'].split(',')
NEW = os.environ.get('NEW', os.environ['COUNTRIES']).split(',')
SAME, FAR, FAR_LEVEL = 30, 300, 600
TWIN = 1500                 # m: a new top this near a database summit of its height is that summit, misplaced
P_MIN = float(os.environ.get('P_MIN', 30))
NEEDLE = 10
BIG = float(os.environ.get('BIG', 5))
LIST_DP = float(os.environ.get('LIST_DP', 30))
PROTECTED = {'Państwowy zasób geodezyjny i kartograficzny', 'Výskum a ochrana Malej Fatry 2017'}
JOINT = 'Joint lidar 1 m (DMR 5.0, DMR 5G, ALS-DGM)'
AUTO = {'DMR 5.0', 'manual measurement from Freemap Slovakia', 'guess', None, 'Wikipedia',
        'DMR 5.0 and NMT EVRF2007 (joint)', JOINT}
LISTS = {'Peaklist', 'FABDEM V1-2'}
NATIVE = {'DMR 5.0'}        # measured on the native grid: the joint grid (resampled to EPSG:3035) re-measures it worse
FLAT = 0.1                  # m: a col this close in height to the run's is the same saddle, wherever along it
BORDER_ONLY = os.environ.get('BORDER_ONLY')      # 'sk': only what data beyond Slovakia changes
COUNTRY = {'DMR 5.0 (SK)': 'sk', 'DMR 5G (CZ)': 'cz', 'ALS-DGM (AT)': 'at'}
DATASET = {'sk': 'DMR 5.0', 'cz': 'DMR 5G (ČÚZK)', 'at': 'ALS-DGM (BEV)'}
DATUM = {'sk': 'Bpv', 'cz': 'Bpv'}                 # else EGM2008
LANG = {'sk': 'sk', 'cz': 'cs', 'at': 'de'}

run = json.load(open(os.environ['RUN']))
# named peaks from OpenStreetMap (`osm_peaks.py`): Slovakia's, and those beside it in /mnt/data/treeline/osm
PEAKS = os.environ.get('PEAKS', ','.join([os.path.expanduser('~/.cache/treeline/lidar/osm/peaks.json'),
                                          '/mnt/data/treeline/osm/peaks-cz.json', '/mnt/data/treeline/osm/peaks-at.json']))
peaks = [p for f in PEAKS.split(',') if os.path.exists(f) for p in json.load(open(f)) if p['kind'] != 'saddle']
to_bpv = Transformer.from_crs('EPSG:4326+3855', 'EPSG:4258+8357', always_xy=True, allow_ballpark=False)


def height(lat, lon, h, country):
    """ (altitude, datum) of an EGM2008 height `h` in `country`'s datum. """
    if DATUM.get(country) == 'Bpv':
        try:
            return round(to_bpv.transform(lon, lat, h, errcheck=True)[2], 2), 'Bpv'
        except ProjError:           # outside the national grids (data reaching beyond the border)
            return None, None
    return round(h, 2), 'EGM2008'


def dist(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * 111320 * k, (a[0] - b[0]) * 110574)


class Near:
    """Points on a 0.01° grid, for those within a limit (at most about 700 m)."""

    def __init__(self, items, key):
        self.key, self.grid = key, collections.defaultdict(list)
        for x in items:
            at = key(x)
            self.grid[(round(at[0], 2), round(at[1], 2))].append(x)

    def all(self, at, limit):
        c = [x for dy in (-0.01, 0, 0.01) for dx in (-0.01, 0, 0.01)
             for x in self.grid.get((round(at[0] + dy, 2), round(at[1] + dx, 2)), [])]
        return sorted((x for x in c if dist(at, self.key(x)) <= limit), key=lambda x: dist(at, self.key(x)))

    def __call__(self, at, limit):
        c = self.all(at, limit)
        return c[0] if c else None


tops = run['summits']
unconverted = 0
for t in tops:
    t['country'] = COUNTRY[t['source']]
    t['alt'], t['datum'] = height(*t['top'], t['h'], t['country'])
    if 'col' in t:
        t['col_country'] = COUNTRY[t['col_source']]
        t['col_alt'], t['col_datum'] = height(*t['col'], t['col_h'], t['col_country'])
        t['p'] = round(t['h'] - t['col_h'], 2)
    else:
        t['p'] = round(t['h'], 2)
    if t['alt'] is None or t.get('col_datum', 'x') is None:
        t['border'] = True                  # not to be written: outside its datum's grid
        unconverted += 1
by_pos = {tuple(t['top']): t for t in tops}
top_near = Near(tops, lambda t: t['top'])
peak_near = Near(peaks, lambda p: (p['lat'], p['lon']))

summits = list(Summit.objects.with_prominence().select_related(
    'point__source', 'point__altitude_datum', 'key_col__point__source', 'key_col__point__altitude_datum',
    'prominence_parent__point', 'prominence_source').prefetch_related('point__countries'))
pos = lambda s: (s.point.location.y, s.point.location.x)
db_near = Near(summits, pos)
named_db = Near([s for s in summits if s.point.name], pos)
FAR_NAMED = float(os.environ.get('NEAR_NAMED', 300))
src = lambda p: p.source.name if p.source_id else None
egm = lambda p: p.altitude_egm2008 if p.altitude_egm2008 is not None else p.altitude

review = collections.defaultdict(list)
points, relinks, new = [], [], {}


def label(s):
    return f'{s.point.name or "unnamed"} #{s.pk}'


def osm_name(at):
    p = peak_near(at, 60)
    if not (p and p.get('name')):
        return None
    base = re.sub(r'\s*\(.*\)$', '', p['name'])
    if Summit.objects.filter(point__location__dwithin=(Point(at[1], at[0], srid=4326), Dm(m=1000)),
                             point__name__iregex=r'^' + re.escape(base) + r'( \(.*\))?$').exists():
        return None
    return p['name']


def fix(point, lat, lon, alt, datum, country):
    return dict(point=point.pk, name=point.name, lat=lat, lon=lon, altitude=alt, datum=datum,
                source=DATASET[country])


def ensure(t, why):
    k = tuple(t['top'])
    if k in new:
        return new[k]['key']
    name = osm_name(k)
    c = t['country']
    entry = dict(key=f'joint:{k[0]},{k[1]}', lat=k[0], lon=k[1], name=name,
                 names=[dict(lang=LANG[c], name=name, local=True)] if name else [],
                 altitude=t['alt'], datum=t['datum'], source=DATASET[c], countries=[c], prominence=t['p'],
                 parent=None, notes=[f'Found in the joint 1 m lidar terrain ({DATASET[c]}; catchment flood), {why}.'])
    if 'col' in t:
        entry['col'] = dict(lat=t['col'][0], lon=t['col'][1], name=None, names=[], altitude=t['col_alt'],
                            datum=t['col_datum'], source=DATASET[t['col_country']], countries=[t['col_country']],
                            notes=[])
    else:
        entry['island'] = True
    new[k] = entry
    entry['parent'] = parent_ref(t)
    return entry['key']


def twin(t):
    """ A database summit within TWIN at `t`'s height, matched to no top of its own. """
    from django.db.models import Q
    here = Point(t['top'][1], t['top'][0], srid=4326)
    c = Summit.objects.filter(point__location__dwithin=(here, Dm(m=TWIN))).filter(
        Q(point__altitude_egm2008__range=(t['h'] - 3, t['h'] + 3)) |
        Q(point__altitude_egm2008__isnull=True, point__altitude__range=(t['h'] - 3, t['h'] + 3))).select_related('point')
    return next((s for s in c if s.pk not in matched), None)


def parent_ref(t):
    """ The run's parent of `t` where it is proven: an existing summit, or a new one. Else None. """
    if 'parent' not in t or not t['parent_proven']:
        return None
    k = tuple(t['parent'])
    pt = by_pos[k]
    s = claimed[k][0] if len(claimed.get(k, [])) == 1 else db_near(k, SAME)
    if s:
        return dict(pk=s.pk, name=s.point.name)
    if named_db(k, FAR_NAMED) or twin(pt) or ('col' in pt and dist(pt['top'], pt['col']) < NEEDLE):
        return None
    return dict(new=ensure(pt, 'the parent of a summit there'))


if BORDER_ONLY:
    from django.contrib.gis.geos import GEOSGeometry
    home = GEOSGeometry(open(os.path.expanduser(f'~/.cache/treeline/lidar/osm/{ {"sk": "slovakia"}[BORDER_ONLY] }.wkt')).read(), srid=4326)
    inside = home.prepared


def crosses(s, t, kc, pr):
    out = lambda lat, lon: not inside.contains(Point(lon, lat, srid=4326))
    at = [t['col']] + ([t['parent']] if 'parent' in t and pr and pr.get('pk') != s.prominence_parent_id else [])
    at += [(kc.location.y, kc.location.x)] if kc else []
    at += [pos(s.prominence_parent)] if s.prominence_parent_id else []
    return any(out(*a) for a in at)


SKIP = set(filter(None, os.environ.get('SKIP', '').split(',')))     # countries another changeset checks
checked = [s for s in summits if any(c.code in COUNTRIES for c in s.point.countries.all())
           and not any(c.code in SKIP for c in s.point.countries.all())]
# matching: a top at its height within 30 m, else within 300 m (moved), else the nearest within 30 m
claimed = collections.defaultdict(list)
pairs = []
for s in sorted(checked, key=lambda s: -egm(s.point)):
    h = egm(s.point)
    tol = 1.5 if s.point.altitude_egm2008 is not None else 3
    near = top_near.all(pos(s), FAR_LEVEL)
    level = [t for t in near if abs(t['h'] - h) <= tol]
    t = next((t for t in level if dist(pos(s), t['top']) <= SAME), None) or \
        next((t for t in level if tuple(t['top']) not in claimed), None) or \
        next((t for t in near if dist(pos(s), t['top']) <= SAME), None)
    if t is None:
        far = near[0] if near else None
        review['no lidar top of P10 near it'].append(dict(
            summit=label(s), alt=s.point.altitude, prom=round(s.prominence or 0, 1), source=src(s.point),
            nearest=None if far is None else dict(at=far['top'], m=round(dist(pos(s), far['top'])), h=far['h'], p=far['p'])))
        continue
    claimed[tuple(t['top'])].append(s)
    pairs.append((s, t))
matched = {s.pk for s, _ in pairs}
twice = {k for k, ss in claimed.items() if len(ss) > 1}
for k in twice:
    review['several database summits on one lidar top (left as they are)'].append(
        dict(top=list(k), h=by_pos[k]['h'], p=by_pos[k]['p'], summits=[label(s) for s in claimed[k]]))

for s, t in pairs:
    if tuple(t['top']) in twice:
        continue
    d = dist(pos(s), t['top'])
    dh = t['h'] - egm(s.point)
    p = s.point
    if d > SAME:
        review['moved onto its top, more than 30 m (applied)'].append(dict(
            summit=label(s), m=round(d), alt=p.altitude, lidar=t['alt']))
    if abs(dh) > BIG:
        review['height off by more than 5 m (left as it is)'].append(dict(
            summit=label(s), alt=p.altitude, lidar=t['alt'], m=round(d, 1), source=src(p),
            p_db=round(s.prominence or 0, 1), p_lidar=t['p']))
        continue
    if t['alt'] is not None and not BORDER_ONLY and src(p) not in PROTECTED and (src(p) not in NATIVE or d > SAME) and (d > SAME or abs(t['alt'] - p.altitude) >= 0.05 or
                                    (p.altitude_datum.code if p.altitude_datum_id else None) != t['datum']):
        points.append(fix(p, t['top'][0], t['top'][1], t['alt'], t['datum'], t['country']))
    if t['border'] or 'col' not in t:
        continue
    kc = s.key_col.point if s.key_col else None
    col_d = dist((kc.location.y, kc.location.x), t['col']) if kc else None
    pr = parent_ref(t)
    if pr is None and s.prominence_parent_id:
        pr = dict(pk=s.prominence_parent_id, name=s.prominence_parent.point.name)
    same_parent = (pr is None and not s.prominence_parent_id) or (
        pr is not None and pr.get('pk') == s.prominence_parent_id)
    col_dh = abs(egm(kc) - t['col_h']) if kc else 0
    same_col = col_d is not None and (col_d <= SAME or (col_dh <= FLAT and col_d <= FAR))
    kc_datum = kc.altitude_datum.code if kc and kc.altitude_datum_id else None
    col_update = kc is not None and src(kc) not in PROTECTED | NATIVE and (
        abs(kc.altitude - t['col_alt']) >= 0.05 or kc_datum != t['col_datum'])
    if col_update and col_dh > BIG and same_col:
        review['key col height off by more than 5 m'].append(dict(
            summit=label(s), col=kc.name, col_db=kc.altitude, col_lidar=t['col_alt'], col_m=round(col_d),
            col_source=src(kc)))
        continue
    col_fix = lambda: fix(kc, t['col'][0], t['col'][1], t['col_alt'], t['col_datum'], t['col_country'])
    if same_col and same_parent:
        if col_update and not BORDER_ONLY:
            points.append(col_fix())
        continue
    psrc = s.prominence_source.name if s.prominence_source_id else None
    what = dict(summit=label(s), p_db=round(s.prominence or 0, 1), p_lidar=t['p'],
                col_m=None if col_d is None else round(col_d), col_db=kc.altitude if kc else None, col_lidar=t['col_alt'],
                parent_db=s.prominence_parent and label(s.prominence_parent),
                parent_lidar=pr and (pr.get('name') or pr.get('new')), prominence_source=psrc)
    if BORDER_ONLY and not crosses(s, t, kc, pr):
        review['inland: differs from the database, not from data beyond the border (not applied)'].append(what)
        continue
    if not (psrc in AUTO or (psrc in LISTS and abs(t['p'] - (s.prominence or 0)) <= LIST_DP)):
        review['key col or parent set by hand, or far from a list\'s'].append(what)
        continue
    taken = Col.objects.filter(point__location__dwithin=(Point(t['col'][1], t['col'][0], srid=4326), Dm(m=SAME)),
                               key_for__isnull=False).exclude(key_for=s).select_related('key_for__point').first()
    if taken:
        review['the 1 m key col is another summit\'s'].append(dict(what, other=label(taken.key_for)))
        continue
    named_col = bool(kc and kc.name and not kc.name.startswith('unnamed'))
    move_old = kc is not None and src(kc) not in PROTECTED and (col_d <= 100 or (col_d <= 300 and not named_col))
    if same_col or move_old:
        key_col = dict(point=kc.pk, name=kc.name)
        if col_update or not same_col:
            points.append(col_fix())
    else:
        if named_col:
            review['named key col replaced, left in place'].append(dict(what, col=kc.name))
        key_col = dict(new=dict(lat=t['col'][0], lon=t['col'][1], altitude=t['col_alt'], datum=t['col_datum'],
                                source=DATASET[t['col_country']], name=None, names=[], countries=[t['col_country']]))
    link = dict(summit=s.pk, summit_name=s.point.name, key_col=key_col)
    if pr is None:
        link['parent'] = None
    elif 'pk' in pr:
        link['parent'] = pr
    else:
        k = next(k for k, e in new.items() if e['key'] == pr['new'])
        link['parent_at'] = dict(lat=k[0], lon=k[1])
    relinks.append(link)

for t in sorted(tops, key=lambda t: -t['p']):
    if t['p'] < P_MIN or t['border'] or t['country'] not in NEW or ('col' in t and dist(t['top'], t['col']) < NEEDLE):
        continue
    if tuple(t['top']) in claimed or db_near(tuple(t['top']), SAME) is not None:
        continue
    named = named_db(tuple(t['top']), FAR_NAMED) if FAR_NAMED > 0 else None
    tw = twin(t)
    if tw:
        review['new top beside a database summit of its height (held: the same peak?)'].append(dict(
            at=t['top'], h=t['h'], p=t['p'], summit=label(tw), m=round(dist(t['top'], pos(tw)))))
        continue
    if named:
        review['new top near a named database summit (held)'].append(dict(
            at=t['top'], alt=t['alt'], p=t['p'], osm=osm_name(tuple(t['top'])), near=label(named),
            m=round(dist(t['top'], pos(named)))))
        continue
    ensure(t, f'P {t["p"]:.1f} m')

# Every reference by number carries where the object will stand when it is applied (`was`).
summit_point = {s.pk: s.point_id for s in summits}
refs = set(p['point'] for p in points)
refs |= {summit_point[l['summit']] for l in relinks}
refs |= {l['key_col']['point'] for l in relinks if 'point' in l['key_col']}
refs |= {summit_point[l['parent']['pk']] for l in relinks if l.get('parent')}
refs |= {summit_point[e['parent']['pk']] for e in new.values() if e['parent'] and 'pk' in e['parent']}
where = {p.pk: [round(p.location.y, 7), round(p.location.x, 7)] for p in NamedPoint.objects.filter(pk__in=refs)}
moved_to = {p['point']: [p['lat'], p['lon']] for p in points}
final = lambda pk: moved_to.get(pk, where[pk])
for p in points:
    p['was'] = where[p['point']]
for l in relinks:
    l['summit_was'] = final(summit_point[l['summit']])
    if 'point' in l['key_col']:
        l['key_col']['was'] = final(l['key_col']['point'])
    if l.get('parent'):
        l['parent']['was'] = final(summit_point[l['parent']['pk']])
for e in new.values():
    if e['parent'] and 'pk' in e['parent']:
        e['parent']['was'] = where[summit_point[e['parent']['pk']]]

# A new summit exactly as high as its parent (a tie at the centimetre): the database wants the parent
# higher, and a summit left without one is worse than a tie broken the wrong way. 1 cm lower, said so.
alt_of = {s.pk: s.point.altitude for s in summits}
ties = 0
for e in sorted(new.values(), key=lambda e: -e['altitude']):     # parents first: a lowered one counts
    par = e['parent']
    if not par:
        continue
    pa = new[next(k for k, x in new.items() if x['key'] == par['new'])]['altitude'] if 'new' in par else alt_of[par['pk']]
    if e['altitude'] >= pa:
        e['notes'].append(f'As high as its parent to the centimetre ({e["altitude"]:.2f} m): put 1 cm lower to break the tie.')
        e['altitude'] = round(pa - 0.01, 2)
        ties += 1
print(f'  {ties} ties with the parent broken (1 cm lower)')

ordered = sorted(new.values(), key=lambda e: -e['prominence'])
meta = dict(sources=dict(lidar=JOINT, names='OpenStreetMap'))
json.dump(dict(meta, summits=ordered), open(os.path.join(D, 'changeset-summits.json'), 'w'), indent=1, ensure_ascii=False)
json.dump(dict(meta, points=points, relinks=relinks), open(os.path.join(D, 'changeset-fixes.json'), 'w'),
          indent=1, ensure_ascii=False)
json.dump(review, open(os.path.join(D, 'review.json'), 'w'), indent=1, ensure_ascii=False)
print(f'{len(checked)} summits in {",".join(COUNTRIES)}; {len(points)} points re-measured; {len(relinks)} relinks; '
      f'{len(new)} new summits ({collections.Counter(e["countries"][0] for e in new.values())})')
print(f'  {unconverted} tops outside their datum grid, not written')
for k, v in review.items():
    print(f'  review: {k}: {len(v)}')
