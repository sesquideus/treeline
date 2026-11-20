from django.contrib.gis.db import models

#: The hierarchies the map draws, by the name its mode radios use, and the parent field each
#: draws towards (`lineageParentAttr` in static/js/map.js).
MAP_MODES = {
    'prominence': 'prominence_parent',
    'isolation': 'isolation_parent',
    'slope': 'slope_parent',
    'horizon': 'horizon_parent',
    'line': 'line_parent',
}


class SummitMapZoom(models.Model):
    """
    Where the global map shows a summit: derived data, rebuilt whole by
    `mountains.map_zooms.refresh_map_zooms` whenever the geographic data has changed.

    `z` is the first zoom it is drawn at by prominence and spacing alone (`first_zooms`);
    `vz_<mode>` lowers that so that in each hierarchy a parent shows as early as its earliest
    child (`visible_zooms`). `edge_<mode>` is the line the map draws from the summit to its
    parent in that hierarchy — through the key col for prominence and line, and the nearest
    higher point for isolation, as with routing on — in Web Mercator, the projection OpenLayers draws
    straight lines in, so that "crosses the view" is asked of exactly what is on screen.

    A side table rather than columns on `Summit`: rebuilding it must not count as an edit of
    the data, which would invalidate every cache and ask for another rebuild.
    """
    summit = models.OneToOneField('Summit', primary_key=True, on_delete=models.CASCADE,
                                  related_name='map_zoom')
    z = models.SmallIntegerField()
    vz_prominence = models.SmallIntegerField(db_index=True)
    vz_isolation = models.SmallIntegerField(db_index=True)
    vz_slope = models.SmallIntegerField(db_index=True)
    vz_horizon = models.SmallIntegerField(db_index=True)
    vz_line = models.SmallIntegerField(db_index=True, default=0)
    edge_prominence = models.LineStringField(srid=3857, null=True)
    edge_isolation = models.LineStringField(srid=3857, null=True)
    edge_slope = models.LineStringField(srid=3857, null=True)
    edge_horizon = models.LineStringField(srid=3857, null=True)
    edge_line = models.LineStringField(srid=3857, null=True)

    def __str__(self):
        return f'map zoom of summit #{self.summit_id}: {self.z}'


class MapZoomVersion(models.Model):
    """
    The data version (`mountains.views.cache.data_version`) the map zooms were last built for:
    one row. In the database, next to the table it describes, rather than in the cache, so the
    two can never disagree — a database restored from a dump, or a test rolled back, takes
    its own record of what it holds with it.
    """
    version = models.CharField(max_length=32)
