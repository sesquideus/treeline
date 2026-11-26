"""
The database's points against OpenStreetMap's borders (`lidar/borders.py`): a changeset (`countries`)
setting each point's countries as the outlines on disk say, for apply_lidar_changeset.

    BOX=minlon,minlat,maxlon,maxlat OUT=countries.json [TOL=100] \
        uv run python manage.py shell < lidar/border_countries.py

A point is decided only where the outlines settle it: inside an outlined country (well away from
its line), or on a line between two outlined countries. A point outside every outline on disk, or on
the line of a country with none, keeps the countries it has that no outline rules out; one left with
none is listed in OUT's `-none.json` for a person.
"""
import json
import os
import sys
from collections import Counter

from django.contrib.gis.geos import Polygon

from mountains.models import NamedPoint

sys.path.insert(0, os.path.join(os.getcwd(), 'lidar'))
from borders import Borders, TOL  # noqa: E402

box = [float(v) for v in os.environ['BOX'].split(',')]
OUT = os.environ['OUT']
b = Borders(tol=float(os.environ.get('TOL', TOL)))
outlined = set(b.codes)
print(len(outlined), 'outlines:', ' '.join(sorted(outlined)), flush=True)

changes, none, stats = [], [], Counter()
pts = NamedPoint.objects.filter(location__intersects=Polygon.from_bbox(box)).prefetch_related('countries')
for p in pts.iterator(chunk_size=5000):
    have = {c.code for c in p.countries.all()}
    lat, lon = p.location.y, p.location.x
    # the outlines' verdict; for the far side of a border no outline covers, what the point has now
    # (minus any outlined country), standing in for the coarse map
    want = b.decide(lat, lon, lambda exclude: have - exclude)
    if want == have:
        continue
    row = dict(point=p.pk, name=p.name, was=[lat, lon], countries=sorted(want), had=sorted(have))
    if not want:
        none.append(row)
        stats['left with none'] += 1
        continue
    changes.append(row)
    stats[f"{'+'.join(sorted(have)) or '-'} -> {'+'.join(sorted(want))}"] += 1
json.dump(dict(sources=dict(lidar='OpenStreetMap borders', names='OpenStreetMap'), countries=changes),
          open(OUT, 'w'), ensure_ascii=False, indent=1)
json.dump(none, open(OUT.replace('.json', '-none.json'), 'w'), ensure_ascii=False, indent=1)
for k, v in stats.most_common(40):
    print(f'{v:6d}  {k}')
print(len(changes), 'points to change,', len(none), 'left with none ->', OUT)
