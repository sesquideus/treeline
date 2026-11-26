"""
The local language's name as the primary name, for summits named from OpenStreetMap: where a node's
`name` is not in the country's language (Crimea's are Russian; on the Romanian border they read
"Stogu / Стіг") and its `name:xx` in that language is there, two changesets for
`apply_lidar_changeset`, applied in this order:

    COUNTRY=ua PEAKS=osm-peaks.json OUT=DIR [RADIUS=400] uv run python manage.py shell < lidar/osm_local_names.py

- DIR/renames.json: each such summit renamed to `name:xx` (a local PointName, source OpenStreetMap).
  Only summits whose name is exactly the node's `name` and whose height or position came from the
  new pipeline (source OpenStreetMap or a model: not a person's measurement) are touched.
- DIR/other-names.json: the node's other `name:yy` tags as PointNames on the renamed summits (applied
  after the renames, which would otherwise drop a name equal to the old one).
"""
import json
import math
import os

from mountains.models import Summit

COUNTRY = os.environ['COUNTRY'].lower()
LANG = {'ua': 'uk'}.get(COUNTRY, COUNTRY)
PEAKS = os.environ['PEAKS']
OUT = os.environ['OUT']
RADIUS = float(os.environ.get('RADIUS', 400))
SOURCES = {'OpenStreetMap', 'GEDTM30 v1.2 (OpenGeoHub, CC BY 4.0)'}


def metres(a, b):
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111200, (a[1] - b[1]) * 111200 * k)


nodes = {}
for p in json.load(open(PEAKS)):
    local = (p.get('other_names') or {}).get(LANG)
    if p.get('name') and local and local != p['name']:
        nodes.setdefault(p['name'], []).append(p)

renames, others = [], []
for s in (Summit.objects.filter(point__countries__code__iexact=COUNTRY, point__source__name__in=SOURCES,
                                point__name__in=list(nodes)).select_related('point').distinct()):
    p = s.point
    here = (p.location.y, p.location.x)
    near = [n for n in nodes[p.name] if metres(here, (n['lat'], n['lon'])) <= RADIUS]
    if len(near) != 1:
        continue                           # none, or two nodes of that name near: left for a person
    n = near[0]
    new = n['other_names'][LANG]
    renames.append(dict(at=list(here), old=p.name, name=new, lang=LANG, source='OpenStreetMap'))
    for lang, name in sorted(n['other_names'].items()):
        if lang != LANG and name and name != new:
            others.append(dict(point=p.pk, point_name=new, lang=lang, name=name, what='summit', source='OpenStreetMap'))
os.makedirs(OUT, exist_ok=True)
json.dump(dict(sources=dict(lidar='GEDTM30 v1.2 (OpenGeoHub, CC BY 4.0)', names='OpenStreetMap'), renames=renames),
          open(os.path.join(OUT, 'renames.json'), 'w'), ensure_ascii=False, indent=1)
json.dump(dict(sources=dict(lidar='GEDTM30 v1.2 (OpenGeoHub, CC BY 4.0)', names='OpenStreetMap'), point_names=others),
          open(os.path.join(OUT, 'other-names.json'), 'w'), ensure_ascii=False, indent=1)
print(f'{len(renames)} summits to rename to their {LANG} name, {len(others)} other-language names -> {OUT}')
