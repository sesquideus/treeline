"""
A `joint.py` run against the database, read only: where the two disagree, for a person to look at.

    RUN=RUN.json OUT=DIR [COUNTRIES=sk,cz,at] [P_NEW=30] uv run python manage.py shell < lidar/joint_db.py

Every database summit in COUNTRIES (and within the run's data) is matched to the run's top: the nearest within 600 m at its
height (EGM2008, within 3 m: a list's coordinates can be hundreds of metres off, and the nearest top then a bump beside the
summit), else the nearest within 30 m. Then, for each pair:
- height: the database's altitude in EGM2008 against the lidar's top (a summit with no EGM2008
  altitude is compared as stored, and says so);
- prominence and key col: only where the run settles the top (its ground stays inside the data);
- parent: only where the run proves it (`parent_proven`: no data beyond can change it).
And the other way: settled tops of P >= P_NEW with no database summit within 300 m.

Writes DIR/compare.json: `stats`, and one list per kind of difference, the largest first.
"""
import collections
import json
import math
import os
import statistics

from mountains.models import Summit

RUN = os.environ['RUN']
D = os.environ['OUT']
os.makedirs(D, exist_ok=True)
COUNTRIES = os.environ.get('COUNTRIES', 'sk,cz,at').split(',')
P_NEW = float(os.environ.get('P_NEW', 30))
SAME, FAR, SAME_H = 30, 600, 3
COUNTRY_OF = {'DMR 5.0 (SK)': 'sk', 'DMR 5G (CZ)': 'cz', 'ALS-DGM (AT)': 'at', 'NMT EVRF2007 (PL)': 'pl',
              'NMT KRON86 (PL)': 'pl', 'FABDEM': 'fabdem'}


def dist(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[1] - b[1]) * 111320 * k, (a[0] - b[0]) * 110574)


class Near:
    """Points on a 0.01° grid, for the nearest within a limit (at most about 700 m)."""

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


run = json.load(open(RUN))
tops = run['summits']
for t in tops:
    t['country'] = COUNTRY_OF.get(t['source'], t['source'])
top_near = Near(tops, lambda t: tuple(t['top']))
by_pos = {tuple(t['top']): t for t in tops}

summits = list(Summit.objects.filter(point__countries__code__in=COUNTRIES).distinct().with_prominence()
               .select_related('point__source', 'key_col__point', 'prominence_parent__point')
               .prefetch_related('point__countries'))
everyone = list(Summit.objects.select_related('point'))
pos = lambda s: (s.point.location.y, s.point.location.x)
db_near = Near(everyone, pos)


def label(s):
    return dict(pk=s.pk, name=s.point.name, at=[round(v, 6) for v in pos(s)],
                countries=sorted(c.code for c in s.point.countries.all()) if hasattr(s, '_prefetched_objects_cache') else None)


def egm(p):
    return (p.altitude_egm2008, True) if p.altitude_egm2008 is not None else (p.altitude, False)


out = collections.defaultdict(list)
claimed = {}
pairs = []
stats = collections.Counter()
for s in summits:
    h, _ = egm(s.point)
    c = [t for t in top_near.all(pos(s), FAR) if abs(t['h'] - h) <= SAME_H]
    t = c[0] if c else top_near(pos(s), SAME)
    how = 'at' if t is not None and dist(pos(s), t['top']) <= SAME else 'moved'
    if t is None:
        near = top_near(pos(s), 150)
        h, ok = egm(s.point)
        if not any(top_near.grid.get((round(pos(s)[0] + dy, 2), round(pos(s)[1] + dx, 2)))
                   for dy in (-0.02, -0.01, 0, 0.01, 0.02) for dx in (-0.02, -0.01, 0, 0.01, 0.02)):
            stats['outside_the_data'] += 1         # no top of the run within about 1.5 km
            continue
        out['no_top'].append(dict(summit=label(s), alt=h, egm2008=ok, prom=s.prominence, nearest=None if near is None else dict(
            at=near['top'], m=round(dist(pos(s), near['top'])), h=near['h'], prom=near['prom'], border=near['border'])))
        continue
    claimed.setdefault(tuple(t['top']), []).append(s)
    pairs.append((s, t, how))

dh_all = []
for s, t, how in pairs:
    d = dist(pos(s), t['top'])
    h, ok = egm(s.point)
    dh = round(t['h'] - h, 2)
    dh_all.append(dh)
    stats['matched'] += 1
    stats['settled'] += not t['border']
    stats['parent_proven'] += t['parent_proven']
    base = dict(summit=label(s), lidar=dict(at=t['top'], h=t['h'], prom=t['prom'], border=t['border'],
                                             source=t['source']), m=round(d, 1))
    if how == 'moved':
        out['off_its_top'].append(dict(base, alt=h))
    if abs(dh) > 1:
        out['height'].append(dict(base, alt=h, egm2008=ok, dh=dh, source=s.point.source.name if s.point.source_id else None))
    if t['border']:
        if s.prominence is not None and s.prominence >= 100:
            out['unsettled_p100'].append(dict(base, prom=s.prominence))
        continue
    p = s.prominence
    if p is not None and abs(t['prom'] - p) > 2:
        out['prominence'].append(dict(base, prom=round(p, 2), dp=round(t['prom'] - p, 2),
                                      prom_source=s.prominence_source.name if getattr(s, 'prominence_source_id', None) else None))
    if s.key_col and 'col' in t:
        cd = dist((s.key_col.point.location.y, s.key_col.point.location.x), t['col'])
        if cd > 50:
            out['key_col'].append(dict(base, col=dict(pk=s.key_col.pk, name=s.key_col.point.name,
                                                     at=[s.key_col.point.location.y, s.key_col.point.location.x],
                                                     alt=egm(s.key_col.point)[0]),
                                       lidar_col=dict(at=t['col'], h=t['col_h']), m_col=round(cd)))
    elif bool(s.key_col) != ('col' in t):
        out['key_col'].append(dict(base, col=None if not s.key_col else dict(pk=s.key_col.pk),
                                   lidar_col=t.get('col'), note='one has no key col (an island high point)'))
    if t['parent_proven']:
        want = None
        if 'parent' in t:                   # the database summit matched to that top, else one standing on it
            q = claimed.get(tuple(t['parent']), [None])[0] or db_near(tuple(t['parent']), SAME)
            want = q.pk if q else 'not in the database'
        have = s.prominence_parent_id
        if want != have and not (want is None and have is None):
            pt = by_pos.get(tuple(t['parent'])) if 'parent' in t else None
            out['parent'].append(dict(base, prom=s.prominence,
                                      parent=dict(pk=have, name=s.prominence_parent.point.name if have else None),
                                      lidar_parent_name=q.point.name if 'parent' in t and q else None,
                                      lidar_parent=None if pt is None else dict(
                                          at=pt['top'], h=pt['h'], prom=pt['prom'],
                                          db=None if want == 'not in the database' else want)))
# the same top claimed by two database summits: duplicates
for k, ss in claimed.items():
    if len(ss) > 1:
        out['two_summits_one_top'].append(dict(top=list(k), h=by_pos[k]['h'], prom=by_pos[k]['prom'],
                                               summits=[label(s) for s in ss]))
# settled tops nobody in the database stands on
missing = collections.Counter()
for t in tops:
    if t['border'] or t['prom'] < P_NEW or tuple(t['top']) in claimed:
        continue
    if db_near(tuple(t["top"]), 300):
        continue
    band = '1000+' if t['prom'] >= 1000 else '300+' if t['prom'] >= 300 else '100+' if t['prom'] >= 100 else f'{P_NEW:g}+'
    missing[(t['country'], band)] += 1
    if t['prom'] >= 100:
        out['not_in_db'].append(dict(at=t['top'], h=t['h'], prom=t['prom'], country=t['country'],
                                     parent_proven=t['parent_proven']))
order = {'height': lambda x: -abs(x['dh']), 'prominence': lambda x: -abs(x['dp']), 'key_col': lambda x: -x.get('m_col', 0),
         'parent': lambda x: -(x['prom'] or 0), 'not_in_db': lambda x: -x['prom'], 'off_its_top': lambda x: -x['m'],
         'no_top': lambda x: -(x['prom'] or 0), 'unsettled_p100': lambda x: -x['prom'],
         'two_summits_one_top': lambda x: -x['prom']}
for k, f in order.items():
    out[k].sort(key=f)
st = dict(run=RUN, countries=COUNTRIES, database=len(summits), run_tops=len(tops),
          run_settled=sum(not t['border'] for t in tops), **stats,
          height_median=statistics.median(dh_all) if dh_all else None,
          height_abs_p95=sorted(abs(x) for x in dh_all)[int(0.95 * len(dh_all))] if dh_all else None,
          counts={k: len(v) for k, v in out.items()},
          not_in_db={f'{c} P{b}': n for (c, b), n in sorted(missing.items())})
json.dump(dict(stats=st, **out), open(os.path.join(D, 'compare.json'), 'w'), indent=1, default=str)
print(json.dumps(st, indent=1, default=str))
