# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Treeline is a GeoDjango application that models the topography of mountains: their
**prominence**, **isolation**, and related hierarchies. It stores summits, cols, rivers,
and confluences as geographic points and exposes them through HTML list/detail pages,
statistics, tree/forest visualizations, and GeoJSON endpoints that feed OpenLayers maps.

Stack: Django 6, Python 3.14, PostGIS (via `django.contrib.gis`), `uv` for dependency
management. Config is read from `.env` through `python-decouple`.

## Commands

```bash
uv sync                              # install deps (incl. editable ../python/django-cairn)
uv run python manage.py runserver    # dev server
uv run python manage.py migrate
uv run python manage.py makemigrations
uv run python manage.py test                       # all tests
uv run python manage.py test mountains             # one app
uv run python manage.py test mountains.tests.Foo   # one test case/method
./db_import.sh                       # dump on amos (via ssh), then DROP + recreate `treeline` here from it
uv run python manage.py refresh_map_zooms   # after migrate on a deploy, and after big imports (see below)
```

Running the tests builds a throwaway `test_treeline` database, which needs two one-time
grants — `kvik` is neither a superuser nor able to create databases by default:

```bash
sudo -u postgres psql -c 'ALTER ROLE kvik CREATEDB'
sudo -u postgres psql -d template1 -c 'CREATE EXTENSION postgis'   # so new DBs inherit it
```

Without the first, `manage.py test` reports "permission denied to create database"; without
the second, it gets as far as `CREATE EXTENSION postgis` and stops at "must be superuser".

### Vertical datums

Every `NamedPoint.altitude` is stored as its source gave it, in `altitude_datum` (a
`core.VerticalDatum`: Bpv, PL-EVRF2007, EGM2008, ETRS89-h, …; null = unknown).
`altitude_egm2008` is that altitude converted to EGM2008, the canonical system, by
`core/functions/heights.py` on every save. Prominence and the other metrics still use the
raw `altitude`. The conversion needs PROJ grids that pyproj does not ship. Fetch them once
per machine (laptop and amos), then fill the column:

```bash
mkdir -p ~/.local/share/proj
for g in sk_gku_Slovakia_ETRS89h_to_Baltic1957 cz_cuzk_CR-2005 pl_gugik_geoid2021-PL-EVRF2007-NH \
         pl_gugik_geoid2011-PL-KRON86-NH us_nga_egm08_25; do
    uv run pyproj sync --file $g --target-directory ~/.local/share/proj
done
# Taiwan's TWVD 2001 has no PROJ grid: the hybrid geoid TWGEOID18h (ISG, CC BY 4.0) as one
uv run python manage.py install_isg_grid \
    https://isgeoid.polimi.it/Geoid/Asia/Taiwan/public/Taiwan_2018_TWGEOID2018h_gravG_20201201.isg tw_isg_twgeoid18h
uv run python manage.py recompute_altitudes
```

Without a grid, a conversion stores null and logs a warning; it never stores the
unconverted number (PROJ's "ballpark" fallback is refused). A datum EPSG cannot route to
EGM2008 names its geoid grid in a PROJ string instead (`+geoidgrids=…`, as TWVD2001 does).
The grids must be readable by the user that uwsgi runs as.

### Lidar (`lidar/`)

Offline tooling, laptop only (`uv sync --group lidar`): Slovakia's DMR 5.0 lidar (1 m, one 156 GB
GeoTIFF in `/mnt/media/treeline/DTM5`, or wherever `TREELINE_DMR5` points) against the database. The server needs none of it — only the
changesets it writes.

```bash
uv run --group lidar python lidar/prominence.py --out OUT   # every P100, blind: ~12 min, cached in ~/.cache/treeline/lidar
cd lidar && uv run --project .. --group lidar python parents.py --out OUT   # TreeLine's prominence parents
LIDAR_DB=db.json uv run python manage.py shell < lidar/export_db.py
cd lidar && uv run --project .. --group lidar python compare.py db.json OUT osm-peaks.json > cmp.json
python3 lidar/report.py cmp.json report.html
uv run --group lidar python lidar/changeset.py cmp.json db.json NEW.json
uv run python manage.py apply_lidar_changeset NEW.json --dry-run   # then without, here and on amos
```

The exact way, which supersedes the pooled `prominence.py`/`parents.py` above: the catchment
engine floods the full 1 m grid in tiles and stitches them (`lidar/catchments.py`, tested against
the one-piece flood in `lidar/test_catchments.py`). DMR 5.0's spikes are filtered first; tops
whose ground reaches the edge of the data are flagged and keep the database's key col and parent.

```bash
cd lidar && uv run --project .. --group lidar python catchments.py dmr5 --out P10.json   # all of Slovakia: ~47 min, 10 workers
CATCHMENTS=lidar/P10.json OUT=DIR uv run python manage.py shell < lidar/catchments_db.py # changesets + DIR/review.json
uv run --group lidar python -m unittest lidar/test_catchments.py
```

`lidar/tiled.py` is the same idea on Copernicus at 1″ for whole continents (South America:
`lidar/southamerica/README.md`).

Beyond the border, Copernicus GLO-30 fills in (a 30 m surface model, forest canopy included):
anything it decides is flagged less precise. Names come from an OpenStreetMap extract (what
Freemap draws). `apply_lidar_changeset` is idempotent and checks each point's name before
touching it, so a changeset can be applied locally, re-imported over, and applied on amos.
Changesets are data, not code: they are not committed (`lidar/changesets/` ignores everything)
but kept in `/mnt/media/treelines/products/` and copied to amos's `lidar/changesets/` with scp.

`.env` (not committed) must define: `SECRET_KEY`, `DB_PASSWORD`, `DEBUG`, `ALLOWED_HOSTS`,
`INTERNAL_IPS`, `EMAIL_PASSWORD`. The database is PostGIS, name `treeline`, user `kvik`,
localhost — see `treeline/settings.py`. `db_import.sh` pulls the SQL dump from a remote host
`amos` over `scp`, so it only works on the maintainer's machine.

## Domain model (the core abstraction)

Everything geographic bottoms out in `mountains.models.point.NamedPoint`: a name, a
PostGIS `PointField` `location` (lon/lat, SRID 4326, geography), and an `altitude`.
`Summit` and `Col` each **wrap** a `NamedPoint` through a `OneToOneField` named `point` —
so a summit's coordinates, name, and altitude are always accessed via `summit.point.*`,
and almost every query must `select_related('point')` to avoid N+1s.

The interesting relationships hang off `Summit` (`mountains/models/summit.py`):

- **Prominence** — `prominence = point.altitude − key_col.point.altitude`. `key_col` is the
  saddle (a `Col`) connecting this summit to higher terrain; `prominence_parent` forms a
  tree of summits. `island_high_point` summits use their full altitude as prominence.
- **Isolation** — distance from the summit to `nearest_higher_point` (a raw `PointField`,
  the closest higher ground), with `isolation_parent` pointing at the summit that ground
  belongs to.
- **Slope** and **Horizon** parents — two more alternative parent hierarchies
  (`slope_parent`, `horizon_parent`/`horizon_parent_std`) with their own geometric metrics.

`Col` links summits (`key_for` reverse of `key_col`) and optionally to a `River` for
hydrological connectivity; a river's `source` and `mouth` are both `NamedPoint`s, so a
junction carries its own name and countries. `River`, `Source`, `Note`, and the
`PointName` (multilingual names) round out the app. `core` provides `Country`/`Language`
and geodesic helpers (`core/functions/world.py`); `users` supplies the custom
`AUTH_USER_MODEL = 'users.User'`.

### The global map loads by view

The map fetches summits and cols tile by tile from `map/features.json`
(`mountains/views/map_features.py`), never the whole database. Which summit shows from which
zoom (spacing 48 px, `core/functions/declutter.py`) and each summit's edge to its parent in every
hierarchy are stored in `SummitMapZoom`, rebuilt whole by `mountains/map_zooms.py`: on the first
map request after a data change, or at once by `refresh_map_zooms` and at the end of
`apply_lidar_changeset`. The data version (`mountains/views/cache.py`) moves once per committed
transaction, never at each write inside one.

## Key patterns to follow

**Composable QuerySet `with_*` methods.** The expensive annotation and prefetch logic lives
in `SummitQuerySet` / `ColQuerySet` as chainable methods (`with_point()`, `with_prominence()`,
`with_isolation()`, `with_slope_parent()`, `with_horizon_parent()`, `with_full_name()`,
`only_complete()`, …). Views compose these rather than writing raw annotations. Metrics like
`prominence`, `isolation`, `slope`, and `angle` become queryset annotations that ordering and
filtering then reference by those names — add new derived metrics as a `with_*` method, not
inline in a view. `Summit.compute_*()` methods prefer an existing annotation before recomputing
in Python.

**`to_dict()` / `to_geojson()` serialization.** Models that appear on maps implement these
(and inherit the `GeoJsonMixin` contract via `GeoModel`). Map views build a GeoJSON
`FeatureCollection` where each feature's `properties.type` (`summit`, `col`, `isolation_point`,
`prominence_line_up/down`, `isolation_line_first/second`, …) drives styling in the frontend
(`static/js/*.js`). Keep that `type` vocabulary consistent between Python and JS.

**Views are packages, not single files.** `mountains/views/` splits into `summit/`, `river/`,
`tree/`, plus `col.py`, `confluence.py`, `statistics.py`. Function-based map builders live in
`mountains/views/__init__.py`; class-based list/detail/JSON views live in the subpackages and
are re-exported so `mountains.urls` can reference `views.summit.X`. Add new views to the
relevant subpackage and re-export through its `__init__.py`.

**`cairn` base classes.** `../python/django-cairn` (editable dependency) provides `AdminModel`
(admin add/change/delete URL helpers — used everywhere via `GeoModel`) and
`OrderableListView` (the base for list views; subclasses define an `ORDERING` dict mapping
query-string keys to ORM field paths and override `parse_get_arguments`/`get_queryset`).
Model `admin.py`, `forms/`, and `views/` are likewise split per-model rather than monolithic.

**Coordinate order.** GeoDjango `Point` is `(x=lon, y=lat)`. `location.y` is latitude,
`location.x` is longitude; the geodesic helpers in `core/functions/world.py` take `(lat, lon)`
tuples. Getting this backwards is the most common bug here.
