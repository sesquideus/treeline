"""
The global map's zoom levels and edges (`SummitMapZoom`), rebuilt from the data.

The map asks for one view at a time (`mountains.views.map_features`), so which summits show at
which zoom cannot be worked out per request: it depends on every summit around, and in each
hierarchy on every descendant. It is worked out here, for all summits at once, and stored.

`ensure_map_zooms()` is what a request calls: it rebuilds when the data has changed since the
last rebuild (the data version, `mountains.views.cache`), and otherwise costs one small query.
`refresh_map_zooms()` rebuilds unconditionally; `manage.py refresh_map_zooms` and the end of
`apply_lidar_changeset` call it, so that after a large import no reader waits for it.
"""
from django.db import connection, transaction

from core.functions.declutter import first_zooms, visible_zooms
from mountains.models import MapZoomVersion, Summit, SummitMapZoom
from mountains.models.summit import MAP_MODES
from mountains.views.cache import data_version

LOCK = 0x7452_4D5A          # pg advisory lock id: two workers never rebuild at once

# Web Mercator stops short of the poles; a summit nearer to one is drawn at the edge.
MAX_LAT = 85.0


def _mercator(geography):
    """SQL for a geography point as a lon/lat geometry Web Mercator can take."""
    g = f'{geography}::geometry'
    return (f'ST_SetSRID(ST_MakePoint(ST_X({g}), GREATEST(LEAST(ST_Y({g}), {MAX_LAT}), -{MAX_LAT})), 4326)')


def _line(*geographies):
    points = ', '.join(_mercator(g) for g in geographies)
    return f'ST_Transform(ST_MakeLine(ARRAY[{points}]), 3857)'


TABLE = SummitMapZoom._meta.db_table
SUMMIT = Summit._meta.db_table
POINT = 'mountains_namedpoint'
COL = 'mountains_col'

def _parent(alias, field):
    return (f'LEFT JOIN {SUMMIT} {alias}s ON {alias}s.id = s.{field}_id '
            f'LEFT JOIN {POINT} {alias} ON {alias}.id = {alias}s.point_id')


#: The edge each hierarchy draws, as SQL over `s` (the summit), `sp` (its point), `kc` (its key
#: col's point) and one point per parent: through the key col for prominence, through the
#: nearest higher point for isolation (or to it alone, where no parent is attributed to it),
#: straight for the others; null where there is nothing to draw.
EDGES = {
    'prominence': f'''CASE WHEN pp.location IS NULL THEN NULL
        WHEN kc.location IS NULL THEN {_line('sp.location', 'pp.location')}
        ELSE {_line('sp.location', 'kc.location', 'pp.location')} END''',
    'isolation': f'''CASE WHEN ip.location IS NULL AND s.nearest_higher_point IS NULL THEN NULL
        WHEN ip.location IS NULL THEN {_line('sp.location', 's.nearest_higher_point')}
        WHEN s.nearest_higher_point IS NULL THEN {_line('sp.location', 'ip.location')}
        ELSE {_line('sp.location', 's.nearest_higher_point', 'ip.location')} END''',
    'slope': f"CASE WHEN slp.location IS NULL THEN NULL ELSE {_line('sp.location', 'slp.location')} END",
    'horizon': f"CASE WHEN hp.location IS NULL THEN NULL ELSE {_line('sp.location', 'hp.location')} END",
    # The line path leaves through the summit's own key col, as the prominence one does.
    'line': f'''CASE WHEN lp.location IS NULL THEN NULL
        WHEN kc.location IS NULL THEN {_line('sp.location', 'lp.location')}
        ELSE {_line('sp.location', 'kc.location', 'lp.location')} END''',
}
PARENT_JOINS = ' '.join((_parent('pp', 'prominence_parent'), _parent('ip', 'isolation_parent'),
                         _parent('slp', 'slope_parent'), _parent('hp', 'horizon_parent'),
                         _parent('lp', 'line_parent')))

#: Every row in one statement, each written once: inserting and then updating four edge
#: columns rewrote every row four more times, with four spatial indexes, and took minutes.
INSERT = f'''
    INSERT INTO {TABLE} (summit_id, z, {', '.join(f'vz_{m}' for m in MAP_MODES)},
                         {', '.join(f'edge_{m}' for m in MAP_MODES)})
    SELECT u.pk, u.z, {', '.join(f'u.vz_{m}' for m in MAP_MODES)},
           {', '.join(f'CASE WHEN ST_NPoints(e.{m}) > 1 THEN e.{m} END' for m in MAP_MODES)}
    FROM unnest(%s::int[], %s::smallint[], {', '.join(['%s::smallint[]'] * len(MAP_MODES))})
         AS u(pk, z, {', '.join(f'vz_{m}' for m in MAP_MODES)})
    JOIN {SUMMIT} s ON s.id = u.pk
    JOIN {POINT} sp ON sp.id = s.point_id
    LEFT JOIN {COL} c ON c.id = s.key_col_id
    LEFT JOIN {POINT} kc ON kc.id = c.point_id
    {PARENT_JOINS}
    CROSS JOIN LATERAL (SELECT {', '.join(f'{sql} AS {m}' for m, sql in EDGES.items())}) e
'''


def zooms():
    """{summit pk: (z, {mode: vz})} for every summit with a position."""
    rows = list(Summit.objects.filter(point__location__isnull=False).values_list(
        'pk', 'point__location', 'point__altitude', 'key_col__point__altitude', 'island_high_point',
        *MAP_MODES.values()))
    z = first_zooms(
        (pk, location.x, location.y,
         # the prominence the map ranks by: the sea is an island high point's col
         altitude if island else (altitude - col if altitude is not None and col is not None else None),
         altitude)
        for pk, location, altitude, col, island, *_ in rows)
    vz = {mode: visible_zooms((row[0], z[row[0]], row[5 + i]) for row in rows)
          for i, mode in enumerate(MAP_MODES)}
    return {pk: (z[pk], {mode: vz[mode][pk] for mode in MAP_MODES}) for pk, *_ in rows}


def refresh_map_zooms():
    """Rebuild the whole table, in one transaction: a reader sees the old one until it commits."""
    computed = zooms()
    pks = list(computed)
    with transaction.atomic(), connection.cursor() as cursor:
        # Two rebuilds at once would each delete what the other has not committed yet and
        # then insert the same rows twice. Waits for one under way; a request does not get
        # here while one is (`ensure_map_zooms`), and the lock is reentrant for it.
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [LOCK])
        cursor.execute(f'DELETE FROM {TABLE}')
        # ST_MakeLine drops a point repeating the one before: a nearest higher point recorded
        # on the summit itself leaves a one-point line, which is no line at all (hence the
        # ST_NPoints above).
        cursor.execute(INSERT, [pks, [computed[pk][0] for pk in pks]]
                       + [[computed[pk][1][mode] for pk in pks] for mode in MAP_MODES])
    return len(pks)


def built_for():
    """The data version the table was last built for, or None."""
    return MapZoomVersion.objects.values_list('version', flat=True).first()


def record(version):
    MapZoomVersion.objects.update_or_create(pk=1, defaults={'version': version})


def refresh_and_record_map_zooms():
    """Rebuild now, and record it as current for the data as it stands."""
    version = data_version()
    with transaction.atomic():
        n = refresh_map_zooms()
        record(version)
    return n


def ensure_map_zooms():
    """
    Rebuild if the data changed since the last rebuild. Returns whether it rebuilt.

    Only one request rebuilds at a time. Any other that finds a rebuild under way is answered
    from the table as it stands, a moment out of date, rather than queued behind it: at
    200 000 summits a rebuild takes half a minute, and a screenful of tiles queued one after
    another would take many times that.
    """
    version = data_version()
    if built_for() == version:
        return False
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s)', [LOCK])
            if not cursor.fetchone()[0]:
                return False
        # Another request may have rebuilt it just before this one took the lock.
        if built_for() == version:
            return False
        refresh_map_zooms()
        record(version)
    return True
