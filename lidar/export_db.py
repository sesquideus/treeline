"""
The database side of the lidar comparison, as JSON for `compare.py`: every summit with its
point, key col, prominence, parent and names by language, and the named points that are
neither summit nor col (hints for uncatalogued peaks).

    LIDAR_DB=/tmp/db.json uv run python manage.py shell < lidar/export_db.py
"""
import json
import os

from mountains.models import NamedPoint, PointName, Summit

names = {}
for n in PointName.objects.values('point_id', 'name', 'language__iso639_1', 'historical'):
    names.setdefault(n['point_id'], []).append(
        dict(name=n['name'], lang=n['language__iso639_1'], historical=n['historical']))


def pt(p):
    if p is None or p.location is None:
        return None
    langs = names.get(p.pk, [])
    return dict(pk=p.pk, name=p.name, lat=p.location.y, lon=p.location.x, alt=p.altitude,
                egm=p.altitude_egm2008, datum=p.altitude_datum.code if p.altitude_datum_id else None,
                countries=[c.code for c in p.countries.all()], langs=langs, names=[n['name'] for n in langs],
                source=p.source.name if p.source_id else None, review=p.altitude_review)


summits = []
for s in (Summit.objects.with_prominence()
          .select_related('point__altitude_datum', 'point__source', 'isolation_source', 'key_col__point__altitude_datum',
                          'key_col__point__source', 'prominence_parent__point')
          .prefetch_related('point__countries', 'key_col__point__countries')):
    point = pt(s.point)
    if point is None:
        continue
    parent = s.prominence_parent
    nhp = s.nearest_higher_point
    summits.append(dict(
        nhp=(nhp.y, nhp.x) if nhp else None, isolation_parent_pk=s.isolation_parent_id,
        isolation_source=s.isolation_source.name if s.isolation_source_id else None,
        isolation_name=s.isolation_name,
        pk=s.pk, point=point, prominence=s.prominence, island=s.island_high_point,
        key_col=pt(s.key_col.point) if s.key_col else None, key_col_pk=s.key_col_id,
        parent=dict(pk=parent.pk, name=parent.point.name, lat=parent.point.location.y,
                    lon=parent.point.location.x) if parent else None))
points = [dict(pk=p.pk, name=p.name, lat=p.location.y, lon=p.location.x, alt=p.altitude)
          for p in NamedPoint.objects.filter(name__isnull=False, location__isnull=False,
                                             summit__isnull=True, col__isnull=True)]
json.dump(dict(summits=summits, points=points), open(os.environ['LIDAR_DB'], 'w'), ensure_ascii=False)
print(f'{len(summits)} summits, {len(points)} other named points -> {os.environ["LIDAR_DB"]}')
