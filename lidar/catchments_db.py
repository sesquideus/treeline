"""
The catchment engine's tops (`catchments.py dmr5`) against the database: changesets for what
only improves, and a review list for what needs a person.

    CATCHMENTS=P10.json OUT=DIR [SCOPE=AREA.wkt] [P_MIN=30] [BIG=5] [NEAR_NAMED=300] uv run python manage.py shell < lidar/catchments_db.py

Writes DIR/changeset-summits.json (new summits: apply first), DIR/changeset-fixes.json (heights,
positions, key cols, parents) and DIR/review.json. The database's Slovak summits are checked (only
those inside SCOPE, if given); new summits of P >= P_MIN are added inside Slovakia (and SCOPE).
Run it again after applying until it finds nothing: a relink refused because its parent's own
key col changes in the same batch goes through the second time.

Never degrade:
- A height changes only where its source is weaker than the lidar, never PZGiK or the Malá Fatra
  research, and only with the height: a few metres along a flat top at the same height is no
  change. A change over 5 m is for review (the point is likely on the wrong top), cols too.
- A summit more than 30 m off its top is moved onto it if a free top within 300 m has its height
  (within 1.5 m).
- Key cols and parents change only where the top's ground stays inside the data (no border, no
  hole), and only where they came from the lidar, Freemap measurements (the same data read by
  hand), a guess, Wikipedia or nowhere. Others are for review. A walk that ends on a top whose
  ground reaches the edge of the data keeps the database's parent (that top's prominence is only
  an upper bound), unless the database knows that top as more prominent anyway.
- Nothing is removed or merged. A key col moves with its summit's saddle (it keeps its name)
  unless it is a named col more than 100 m off, which stays where it is for review.
- New summits: not reaching the edge of the data, no needles (key col within 10 m: a cone of bad
  data), none within 300 m of a named database summit (a misplaced one, or a tower to look at),
  OSM names unless a database summit of that name stands within 1 km.
"""
import collections
import json
import math
import os
import re

from django.contrib.gis.geos import GEOSGeometry, Point
from django.contrib.gis.measure import D as Dm
from pyproj import Transformer

from mountains.models import Col, Summit

D = os.environ['OUT']
os.makedirs(D, exist_ok=True)
SCOPE = os.environ.get('SCOPE')
SAME = 30
P_MIN = float(os.environ.get('P_MIN', 30))
NEEDLE = 10
BIG = float(os.environ.get("BIG", 5))     # m: a height change beyond this is for review
PROTECTED = {'Państwowy zasób geodezyjny i kartograficzny', 'Výskum a ochrana Malej Fatry 2017'}
AUTO = {'DMR 5.0', 'manual measurement from Freemap Slovakia', 'guess', None, 'Wikipedia'}
LIDAR = 'DMR 5.0'

lidar = json.load(open(os.environ['CATCHMENTS']))
slovakia = GEOSGeometry(open(os.path.expanduser('~/.cache/treeline/lidar/osm/slovakia.wkt')).read(), srid=4326).prepared
scope = GEOSGeometry(open(SCOPE).read(), srid=4326).prepared if SCOPE else None
in_scope = lambda p: scope is None or scope.contains(p)
peaks = [p for p in json.load(open(os.path.expanduser('~/.cache/treeline/lidar/osm/peaks.json'))) if p['kind'] != 'saddle']
to_bpv = Transformer.from_crs('EPSG:4937', 'EPSG:4258+8357', always_xy=True, allow_ballpark=False)


def bpv(lat, lon, h):
    return round(to_bpv.transform(lon, lat, h)[2], 2)


def dist(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * 111320 * k, (a[0] - b[0]) * 110574)


class Near:
    """Points on a 0.01° grid, for the nearest within a limit."""

    def __init__(self, items, key):
        self.key, self.grid = key, collections.defaultdict(list)
        for x in items:
            at = key(x)
            self.grid[(round(at[0], 2), round(at[1], 2))].append(x)

    def __call__(self, at, limit):
        c = [x for dy in (-0.01, 0, 0.01) for dx in (-0.01, 0, 0.01)
             for x in self.grid.get((round(at[0] + dy, 2), round(at[1] + dx, 2)), [])]
        best = min(c, key=lambda x: dist(at, self.key(x)), default=None)
        return best if best is not None and dist(at, self.key(best)) <= limit else None


tops = lidar['summits']
for t in tops:
    t['alt'] = bpv(*t['top'], t['h'])
    if 'col' in t:
        t['col_alt'] = bpv(*t['col'], t['col_h'])
        t['p'] = round(t['alt'] - t['col_alt'], 2)
    else:
        t['p'] = t['alt']
by_pos = {tuple(t['top']): t for t in tops}
top_near = Near(tops, lambda t: t['top'])
peak_near = Near(peaks, lambda p: (p['lat'], p['lon']))

summits = list(Summit.objects.with_prominence().select_related(
    'point__source', 'key_col__point__source', 'prominence_parent__point', 'prominence_source')
    .prefetch_related('point__countries'))
pos = lambda s: (s.point.location.y, s.point.location.x)
db_near = Near(summits, pos)
src = lambda p: p.source.name if p.source_id else None

review = collections.defaultdict(list)
points, relinks, new = [], [], {}


def osm_name(at):
    p = peak_near(at, 60)
    if not (p and p.get('name')):
        return None
    base = re.sub(r'\s*\(.*\)$', '', p['name'])
    if Summit.objects.filter(point__location__dwithin=(Point(at[1], at[0], srid=4326), Dm(m=1000)),
                             point__name__iregex=r'^' + re.escape(base) + r'( \(.*\))?$').exists():
        return None
    return p['name']


def ensure(t, why):
    k = tuple(t['top'])
    if k in new:
        return new[k]['key']
    name = osm_name(k)
    entry = dict(key=f'lidar:{k[0]},{k[1]}', lat=k[0], lon=k[1], name=name,
                 names=[dict(lang='sk', name=name, local=True)] if name else [],
                 altitude=t['alt'], datum='Bpv', countries=['sk'], prominence=t['p'], parent=None,
                 notes=[f'Found in DMR 5.0 at 1 m (catchment flood), {why}.'])
    if 'col' in t:
        entry['col'] = dict(lat=t['col'][0], lon=t['col'][1], name=None, names=[], altitude=t['col_alt'],
                            datum='Bpv', countries=['sk'], notes=[])
    else:
        entry['island'] = True
    new[k] = entry
    entry['parent'] = parent_ref(t)
    return entry['key']


def parent_ref(t):
    if 'parent' not in t:
        return None
    pt = by_pos[tuple(t['parent'])]
    s = db_near(tuple(t['parent']), SAME)
    if s is None and tuple(t['parent']) in claimed:
        s = claimed[tuple(t['parent'])][0]
    if pt['border']:
        # Its prominence here is only an upper bound, so the walk may have stopped there
        # wrongly — unless the database knows it as more prominent all the same.
        return dict(pk=s.pk, name=s.point.name) if s and (s.prominence or 0) > t['p'] else None
    if s:
        return dict(pk=s.pk, name=s.point.name)
    if named_near(tuple(t['parent'])):
        return None
    if pt['border'] or ('col' in pt and dist(pt['top'], pt['col']) < NEEDLE):
        return None
    return dict(new=ensure(pt, 'the parent of a summit there'))


named_db = Near([s for s in summits if s.point.name], pos)


def named_near(at):
    return named_db(at, FAR_NAMED) if FAR_NAMED > 0 else None


FAR_NAMED = float(os.environ.get('NEAR_NAMED', 300))   # 0: a reviewed area, nothing held


def label(s):
    return f'{s.point.name or "unnamed"} #{s.pk}'


slovak = [s for s in summits if any(c.code == 'sk' for c in s.point.countries.all()) and in_scope(s.point.location)]
claimed = collections.defaultdict(list)
pairs = []
for s in slovak:
    t = top_near(pos(s), SAME)
    if t is not None:
        claimed[tuple(t['top'])].append(s)
        pairs.append((s, t))
# A summit placed more than 30 m off its top: the nearest free top within 300 m at its height.
FAR, SAME_H = 300, 1.5
for s in slovak:
    if any(s is x for x, _ in pairs):
        continue
    c = [t for t in tops if dist(pos(s), t['top']) <= FAR and abs(t['alt'] - s.point.altitude) <= SAME_H
         and tuple(t['top']) not in claimed] if top_near(pos(s), FAR) else []
    if c:
        t = min(c, key=lambda t: dist(pos(s), t['top']))
        claimed[tuple(t['top'])].append(s)
        pairs.append((s, t))
        review['moved onto its top, more than 30 m (applied)'].append(dict(
            summit=label(s), m=round(dist(pos(s), t['top'])), alt=s.point.altitude, lidar=t['alt']))
    else:
        far = top_near(pos(s), 150)
        review['no lidar top of P10 within 30 m'].append(dict(
            summit=label(s), alt=s.point.altitude, prom=round(s.prominence or 0, 1), source=src(s.point),
            nearest=None if far is None else dict(at=far['top'], m=round(dist(pos(s), far['top'])), alt=far['alt'], p=far['p'])))
for s, t in sorted(pairs, key=lambda st: -st[0].point.altitude):
    d = dist(pos(s), t['top'])
    dh = t['alt'] - s.point.altitude
    if src(s.point) in PROTECTED:
        pass
    elif abs(dh) > BIG:
        review['height off by more than 5 m'].append(dict(summit=label(s), alt=s.point.altitude, lidar=t['alt'],
                                                          moved_m=round(d, 1), source=src(s.point)))
    elif abs(dh) >= 0.05:      # a few metres along a flat top at the same height is no change
        points.append(dict(point=s.point.pk, name=s.point.name, lat=t['top'][0], lon=t['top'][1],
                           altitude=t['alt'], datum='Bpv'))
    if t['border'] or 'col' not in t:
        continue
    kc = s.key_col.point if s.key_col else None
    col_d = dist((kc.location.y, kc.location.x), t['col']) if kc else None
    pr = parent_ref(t)
    if pr is None and s.prominence_parent_id:
        pr = dict(pk=s.prominence_parent_id, name=s.prominence_parent.point.name)
    same_parent = (pr is None and not s.prominence_parent_id) or (
        pr is not None and 'pk' in pr and pr['pk'] == s.prominence_parent_id)
    same_col = col_d is not None and col_d <= SAME
    col_update = kc is not None and src(kc) not in PROTECTED and abs(kc.altitude - t['col_alt']) >= 0.05
    if col_update and abs(kc.altitude - t['col_alt']) > BIG:
        review['key col height off by more than 5 m'].append(dict(
            summit=label(s), col=kc.name, col_db=kc.altitude, col_lidar=t['col_alt'], col_m=round(col_d),
            col_source=src(kc)))
        continue
    if same_col and same_parent:
        if col_update:
            points.append(dict(point=kc.pk, name=kc.name, lat=t['col'][0], lon=t['col'][1],
                               altitude=t['col_alt'], datum='Bpv'))
        continue
    psrc = s.prominence_source.name if s.prominence_source_id else None
    what = dict(summit=label(s), p_db=round(s.prominence or 0, 1), p_lidar=t['p'],
                col_m=None if col_d is None else round(col_d), col_db=kc.altitude if kc else None, col_lidar=t['col_alt'],
                parent_db=s.prominence_parent and label(s.prominence_parent),
                parent_lidar=pr and (pr.get('name') or pr.get('new')), prominence_source=psrc)
    if psrc not in AUTO:
        review['key col or parent set by hand'].append(what)
        continue
    taken = Col.objects.filter(point__location__dwithin=(Point(t['col'][1], t['col'][0], srid=4326), Dm(m=SAME)),
                               key_for__isnull=False).exclude(key_for=s).select_related('key_for__point').first()
    if taken:
        review['the 1 m key col is another summit\'s'].append(dict(what, other=label(taken.key_for)))
        continue
    named_col = bool(kc and kc.name and not kc.name.startswith('unnamed'))    # 'unnamed (X)' is no name
    move_old = kc is not None and src(kc) not in PROTECTED and (col_d <= 100 or (col_d <= 300 and not named_col))
    if same_col or move_old:
        key_col = dict(point=kc.pk, name=kc.name)
        if col_update or not same_col:
            points.append(dict(point=kc.pk, name=kc.name, lat=t['col'][0], lon=t['col'][1],
                               altitude=t['col_alt'], datum='Bpv'))
    else:
        if named_col:
            review['named key col replaced, left in place'].append(dict(what, col=kc.name))
        key_col = dict(new=dict(lat=t['col'][0], lon=t['col'][1], altitude=t['col_alt'], datum='Bpv',
                                name=None, names=[], countries=['sk']))
    link = dict(summit=s.pk, summit_name=s.point.name, key_col=key_col)
    if pr is None:
        link['parent'] = None           # it had none, and the walk found none it can trust
    elif 'pk' in pr:
        link['parent'] = pr
    else:
        k = next(k for k, e in new.items() if e['key'] == pr['new'])
        link['parent_at'] = dict(lat=k[0], lon=k[1])
    relinks.append(link)

for k, ss in claimed.items():
    if len(ss) > 1:
        review['several database summits on one lidar top'].append([label(s) for s in ss])

for t in tops:
    if t['p'] < P_MIN or t['border'] or ('col' in t and dist(t['top'], t['col']) < NEEDLE):
        continue
    if tuple(t['top']) in claimed or db_near(tuple(t['top']), SAME) is not None:
        continue
    here = Point(t['top'][1], t['top'][0], srid=4326)
    if not slovakia.contains(here) or not in_scope(here):
        continue
    named = named_near(tuple(t['top']))
    if named:
        review['new top near a named database summit (held)'].append(dict(
            at=t['top'], alt=t['alt'], p=t['p'], osm=osm_name(tuple(t['top'])), near=label(named),
            m=round(dist(t['top'], pos(named)))))
        continue
    ensure(t, f'P {t["p"]:.1f} m')

# DMR 5.0 artefacts lie on its own grid lines: new tops sharing a raster row or column (mod 1 km)
# far more often than chance are listed.
to_tm = Transformer.from_crs('EPSG:4326', 'EPSG:3046', always_xy=True)
lines = collections.Counter()
for e in new.values():
    x, y = to_tm.transform(e['lon'], e['lat'])
    e['_rc'] = (int(5497220 - y) % 1000, int(x - 191148) % 1000)
    lines['row', e['_rc'][0]] += 1
    lines['col', e['_rc'][1]] += 1
expect = 2 * len(new) / 1000 + 1
suspect = {k for k, n in lines.items() if n > 5 * expect}
for e in new.values():
    rc = e.pop('_rc')
    if ('row', rc[0]) in suspect or ('col', rc[1]) in suspect:
        review['new summit on a suspect DMR 5.0 grid line'].append(dict(at=[e['lat'], e['lon']], alt=e['altitude'], p=e['prominence']))
review['suspect grid lines'] = [dict(kind=k, mod=m, tops=lines[k, m]) for k, m in sorted(suspect)]

# Every reference by number carries where the object will stand when it is applied (`was`):
# numbers are not shared with another database once either has made something the other has
# not, and the apply command takes an unnamed point by its number only where it stands.
from mountains.models import NamedPoint  # noqa: E402
summit_point = {s.pk: s.point_id for s in summits}
refs = set(p['point'] for p in points)
refs |= {summit_point[l['summit']] for l in relinks}
refs |= {l['key_col']['point'] for l in relinks if 'point' in l['key_col']}
refs |= {summit_point[l['parent']['pk']] for l in relinks if l.get('parent')}
refs |= {summit_point[e['parent']['pk']] for e in new.values() if e['parent'] and 'pk' in e['parent']}
where = {p.pk: [round(p.location.y, 7), round(p.location.x, 7)] for p in NamedPoint.objects.filter(pk__in=refs)}
moved_to = {p['point']: [p['lat'], p['lon']] for p in points}
final = lambda pk: moved_to.get(pk, where[pk])       # points are moved before the relinks run
for p in points:
    p['was'] = where[p['point']]
for l in relinks:
    l['summit_was'] = final(summit_point[l['summit']])
    if 'point' in l['key_col']:
        l['key_col']['was'] = final(l['key_col']['point'])
    if l.get('parent'):
        l['parent']['was'] = final(summit_point[l['parent']['pk']])
for e in new.values():                               # new summits go in before anything moves
    if e['parent'] and 'pk' in e['parent']:
        e['parent']['was'] = where[summit_point[e['parent']['pk']]]

ordered = sorted(new.values(), key=lambda e: -e['prominence'])
meta = dict(sources=dict(lidar=LIDAR, names='OpenStreetMap'))
json.dump(dict(meta, summits=ordered), open(os.path.join(D, 'changeset-summits.json'), 'w'), indent=1, ensure_ascii=False)
json.dump(dict(meta, points=points, relinks=relinks), open(os.path.join(D, 'changeset-fixes.json'), 'w'),
          indent=1, ensure_ascii=False)
json.dump(review, open(os.path.join(D, 'review.json'), 'w'), indent=1, ensure_ascii=False)
print(f'{len(slovak)} Slovak summits; {len(points)} points re-measured; {len(relinks)} relinks; {len(new)} new summits')
for k, v in review.items():
    print(f'  review: {k}: {len(v)}')
