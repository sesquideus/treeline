"""
A flat index on the locations, for the map's lon/lat rectangles (`mountains.views.viewport.in_bbox`):
the expression is the one Django writes for `Cast('location', GeometryField(srid=4326))`, so the
planner can use it. Plain SQL: the model layer's GistIndex wants django.contrib.postgres installed.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('mountains', '0046_map_zoom_line')]

    operations = [
        migrations.RunSQL(
            'CREATE INDEX namedpoint_location_flat ON mountains_namedpoint '
            'USING gist (((location)::geometry(GEOMETRY,4326)))',
            'DROP INDEX namedpoint_location_flat'),
    ]
