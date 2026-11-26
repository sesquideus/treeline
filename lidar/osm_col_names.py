"""
Names (and heights) for the database's unnamed key cols from the named saddles of an OpenStreetMap
extract (`osm_peaks.py` output, kind 'saddle'), as a changeset for `apply_lidar_changeset`.

    COUNTRY=ua PEAKS=osm-peaks.json OUT=col-names.json [RADIUS=250] [AGREE=20] [KIND=summit] \
        uv run python manage.py shell < lidar/osm_col_names.py

KIND=summit does the same for the database's unnamed summits (the FABDEM world import left many)
from the named OSM peaks, RADIUS 150 by default; a name another point already holds is not taken
but listed (it may be a duplicate summit).

- A key col of COUNTRY takes the nearest named OSM saddle within RADIUS metres; a saddle names one
  col at most (the nearest). Named cols are left alone.
- Its name: the saddle's `name:xx` in the country's language, else `name`, as the col's name and a local PointName in the country's language,
  its `name:xx` tags as PointNames, all sourced to OpenStreetMap.
- Its height, by the altitude rule: where the saddle gives one within AGREE metres of the col's, the
  col takes it (source OpenStreetMap, altitude_review O); beyond AGREE it keeps its own and is marked
  W with a note giving both. A col a person marked R is never touched (`apply_lidar_changeset`).
"""
import json
import math
import os

from mountains.models import Col, NamedPoint, Summit

COUNTRY = os.environ['COUNTRY'].lower()
PEAKS = os.environ['PEAKS']
OUT = os.environ['OUT']
KIND = os.environ.get('KIND', 'col')
RADIUS = float(os.environ.get('RADIUS', 250 if KIND == 'col' else 150))
AGREE = float(os.environ.get('AGREE', 20))
LANG = {'ua': 'uk'}.get(COUNTRY, COUNTRY)


def ele(v):
    try:
        return float(str(v).replace(',', '.').split()[0].rstrip('m'))
    except (TypeError, ValueError, IndexError):
        return None


def metres(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111200, (a[1] - b[1]) * 111200 * k)


def summit_clash(s, h):
    """Why the summit cannot take height h without breaking an invariant, or None."""
    if s.key_col is not None and s.key_col.point.altitude >= h:
        return 'it would be no higher than its key col'
    new_p = h - s.key_col.point.altitude if s.key_col is not None else None
    par = s.prominence_parent
    if par is not None:
        if par.point.altitude <= h:
            return f'it would be as high as its parent {par.point}'
        pp = par.compute_prominence()
        if new_p is not None and pp is not None and pp <= new_p:
            return f'it would be as prominent as its parent {par.point}'
    for ch in s.prominence_children.select_related('point', 'key_col__point'):
        if ch.point.altitude >= h:
            return f'{ch.point} hanging from it would be as high'
        cp = ch.compute_prominence()
        if new_p is not None and cp is not None and cp >= new_p:
            return f'{ch.point} hanging from it would be as prominent'
    for rel in ('isolation_parent', 'slope_parent', 'horizon_parent'):
        if Summit.objects.filter(**{rel: s}, point__altitude__gte=h).exists():
            return f'a summit with it as {rel.replace("_", " ")} would be as high'
    ip = s.isolation_parent
    if ip is not None and ip.point.altitude <= h:
        return f'it would be as high as its isolation parent {ip.point}'
    return None


saddles = [p for p in json.load(open(PEAKS)) if p.get('kind') == ('saddle' if KIND == 'col' else 'peak') and p.get('name')]
grid = {}
for s in saddles:
    grid.setdefault((math.floor(s['lat'] * 50), math.floor(s['lon'] * 50)), []).append(s)

if KIND == 'col':
    cols = list(Col.objects.filter(point__countries__code__iexact=COUNTRY, key_for__isnull=False, point__name__isnull=True)
                .select_related('point', 'key_for__point'))
else:
    cols = list(Summit.objects.filter(point__countries__code__iexact=COUNTRY, point__name__isnull=True)
                .select_related('point', 'key_col__point', 'prominence_parent__point'))
pairs = []
for c in cols:
    here = (c.point.location.y, c.point.location.x)
    gy, gx = math.floor(here[0] * 50), math.floor(here[1] * 50)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for s in grid.get((gy + dy, gx + dx), []):
                d = metres(here, (s['lat'], s['lon']))
                if d <= RADIUS:
                    pairs.append((d, c, s))
pairs.sort(key=lambda x: x[0])
used_col, used_saddle = set(), set()
col_names, points, notes, taken = [], [], 0, []
for d, c, s in pairs:
    if c.pk in used_col or s['id'] in used_saddle:
        continue
    used_col.add(c.pk), used_saddle.add(s['id'])
    p = c.point
    summit = c.key_for if KIND == 'col' else c   # the summit whose prominence the height moves
    # the name in the country's language where OSM has it (Crimea's `name` is Russian, the Romanian
    # border's reads "Stogu / Стіг"), else `name`; the other `name:xx` tags as names of their own
    other = s.get('other_names') or {}
    primary = other.get(LANG) or s['name']
    names = [dict(lang=k, name=v) for k, v in sorted(other.items()) if v and v != primary and k != LANG]
    if KIND == 'summit':
        holder = NamedPoint.objects.filter(name=primary).first()
        if holder is not None:
            taken.append(dict(point=p.pk, was=[p.location.y, p.location.x], altitude=p.altitude, name=primary,
                              holder=holder.pk, holder_at=[holder.location.y, holder.location.x],
                              holder_altitude=holder.altitude))
            continue
    col_names.append(dict(point=p.pk, was=[p.location.y, p.location.x], summit=str(summit.point if summit else ''),
                          name=primary, lang=LANG, names=names, source='OpenStreetMap'))
    h = ele(s.get('ele'))
    if h is None:
        continue
    fix = dict(point=p.pk, name=None, was=[p.location.y, p.location.x])
    # the summit's prominence with the OSM height: it must stay below its parent's and above its
    # children's, or the prominence tree breaks (a parent must be more prominent than its children)
    clash = None
    if KIND == 'summit':
        clash = summit_clash(c, h)
    elif summit is not None:
        new_p = summit.point.altitude - h
        parent = summit.prominence_parent
        if parent is not None and parent.compute_prominence() is not None and parent.compute_prominence() <= new_p:
            clash = f'{summit.point} would be as prominent as its parent {parent.point}'
        lesser = [c for c in summit.prominence_children.select_related('point', 'key_col__point')
                  if c.compute_prominence() is not None and c.compute_prominence() >= new_p]
        if lesser:
            clash = f'{summit.point} (P{new_p:.0f}) would be less prominent than {len(lesser)} summit(s) hanging from it'
    if abs(h - p.altitude) <= AGREE and clash:
        fix.update(altitude_review='W', notes=[f'OpenStreetMap gives {s["name"]} {h:g} m, the model {p.altitude:.1f} m: '
                                               f'{abs(h - p.altitude):.1f} m apart. The OSM height is not taken: {clash}.'])
        notes += 1
    elif abs(h - p.altitude) <= AGREE:
        fix.update(altitude=round(h, 2), datum=None, source='OpenStreetMap', altitude_review='O')
    else:
        fix.update(altitude_review='W', notes=[f'OpenStreetMap gives {s["name"]} {h:g} m, the model {p.altitude:.1f} m: '
                                               f'{abs(h - p.altitude):.1f} m apart. Kept the model\'s height.'])
        notes += 1
    points.append(fix)
if taken:
    json.dump(taken, open(OUT.replace('.json', '-taken.json'), 'w'), ensure_ascii=False, indent=1)
json.dump(dict(sources=dict(lidar='GEDTM30 v1.2 (OpenGeoHub, CC BY 4.0)', names='OpenStreetMap'),
               points=points, col_names=col_names), open(OUT, 'w'), ensure_ascii=False, indent=1)
print(f'{len(cols)} unnamed key cols in {COUNTRY}, {len(saddles)} named OSM saddles: {len(col_names)} named, '
      f'{sum(1 for f in points if f.get("altitude") is not None)} heights from OSM (O), {notes} warnings (W), {len(taken)} names already taken -> {OUT}')
