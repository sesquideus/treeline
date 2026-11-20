"""
Line parents (`Summit.line_parent`) as a changeset, from either engine's output:

    LINE=P10.json CUTOFF=30 SOURCE='DMR 5.0' OUT=lines.json [RADIUS=150] \
        uv run python manage.py shell < lidar/line_changeset.py

LINE is `catchments.py dmr5` output ({summits: [{top, prom, border, line}]}) or
`southamerica/line_parents.py` output ([{lat, lon, line}]). Each top of at least CUTOFF is
the database summit nearest it within RADIUS metres, each summit claimed by its nearest top
only; a link is written where both ends are found, by where the database's summits stand (ids
are not shared with another database). Skipped, and counted: a top whose ground reaches the
edge of the data (the walk may have missed what lies beyond), and an end not in the database.
"""
import json
import os
from collections import Counter

from django.contrib.gis.geos import Polygon

from mountains.models import Summit

src = json.load(open(os.environ['LINE']))
CUTOFF = float(os.environ['CUTOFF'])
SOURCE = os.environ['SOURCE']
RADIUS = float(os.environ.get('RADIUS', 150))
OUT = os.environ['OUT']

if isinstance(src, dict):
    tops = [dict(at=tuple(x['top']), line=tuple(x['line']) if 'line' in x else None,
                 edge=x.get('border', False))
            for x in src['summits'] if x['prom'] >= CUTOFF]
else:
    tops = [dict(at=(x['lat'], x['lon']), line=tuple(x['line']) if x['line'] else None, edge=x.get('edge', False))
            for x in src]
print(len(tops), 'tops of P >=', CUTOFF)

lats = [t['at'][0] for t in tops]
lons = [t['at'][1] for t in tops]
box = Polygon.from_bbox((min(lons) - 0.05, min(lats) - 0.05, max(lons) + 0.05, max(lats) + 0.05))
box.srid = 4326
summits = list(Summit.objects.filter(point__location__intersects=box)
               .values_list('pk', 'point__location', 'point__altitude'))
print(len(summits), 'database summits around')

KM_LAT = 110.574


def metres(a, b):
    from math import cos, hypot, radians
    k = cos(radians((a[0] + b[0]) / 2))
    return hypot((a[0] - b[0]) * KM_LAT, (a[1] - b[1]) * 111.32 * k) * 1000


CELL = 0.01
grid = {}
for pk, loc, alt in summits:
    grid.setdefault((int(loc.y // CELL), int(loc.x // CELL)), []).append((pk, (loc.y, loc.x), alt))


def nearest(at):
    i, j = int(at[0] // CELL), int(at[1] // CELL)
    best = None
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for pk, ll, alt in grid.get((i + di, j + dj), []):
                d = metres(at, ll)
                if d <= RADIUS and (best is None or d < best[0]):
                    best = (d, pk, ll, alt)
    return best


# Each summit to its nearest top: a second top near it is some other hump.
claim = {}
for t in tops:
    n = nearest(t['at'])
    if n and (n[1] not in claim or n[0] < claim[n[1]][0]):
        claim[n[1]] = (n[0], t['at'])
summit_of = {at: pk for pk, (_, at) in claim.items()}
where = {pk: (round(loc.y, 7), round(loc.x, 7)) for pk, loc, _ in summits}
height = {pk: alt for pk, _, alt in summits}

stats = Counter()
links = []
for t in tops:
    s = summit_of.get(t['at'])
    if s is None:
        stats['top not in the database'] += 1
        continue
    if t['edge']:
        stats['ground reaches the edge of the data'] += 1
        continue
    if t['line'] is None:
        links.append(dict(summit_at=list(where[s]), parent_at=None, cutoff=CUTOFF, source=SOURCE))
        stats['none (island high point)'] += 1
        continue
    p = summit_of.get(t['line'])
    if p is None:
        stats['line parent not in the database'] += 1
        continue
    if height[p] is not None and height[s] is not None and height[p] <= height[s]:
        stats['line parent not higher in the database'] += 1
        continue
    links.append(dict(summit_at=list(where[s]), parent_at=list(where[p]), cutoff=CUTOFF, source=SOURCE))
    stats['linked'] += 1

json.dump(dict(sources=dict(lidar=SOURCE, names='OpenStreetMap'), line_parents=links), open(OUT, 'w'), indent=0)
for k, v in stats.most_common():
    print(f'  {v:7d} {k}')
print(len(links), 'line parents ->', OUT)
