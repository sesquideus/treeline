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
./db_import.sh                       # DROP + recreate `treeline` DB from a pg_restore dump
```

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

`Col` links summits (`key_for` reverse of `key_col`) and optionally to a `River`/`Confluence`
for hydrological connectivity. `River`, `Confluence`, `Source`, `Note`, and the
`PointName` (multilingual names) round out the app. `core` provides `Country`/`Language`
and geodesic helpers (`core/functions/world.py`); `users` supplies the custom
`AUTH_USER_MODEL = 'users.User'`.

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
