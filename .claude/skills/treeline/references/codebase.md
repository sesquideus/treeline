# Working in the code

## Layout

```
core/          Country, Language, geodesic helpers (functions/world.py), PointFormField
mountains/     the domain app
  models/      point.py, summit/model.py, col.py, river.py, confluence.py, source.py, note.py
  views/       summit/, river/, tree/, col/, confluence.py, statistics.py, __init__.py
  forms/  admin/  templatetags/  templates/
users/         custom AUTH_USER_MODEL
static/js/     map.js, styles.js, rivers.js, summit-detail-map.js  (OpenLayers 9, not Leaflet)
```

`admin/`, `forms/`, `models/`, `views/` are packages split per model, re-exporting through
`__init__.py`. Add a new model's admin to `mountains/admin/<model>.py` and export it there;
same for forms and views. `mountains/urls.py` reaches views as `views.summit.X`, so anything
routed must be re-exported up the chain.

Dependencies come from `uv`, including the editable `../python/django-cairn`, which supplies
`AdminModel` (admin URL helpers, inherited via `GeoModel`), `ModelAdmin`, `admin_action`, and
`OrderableListView`. When list-view ordering behaves oddly, read
`../python/django-cairn/cairn/views.py` — its docstring is the spec.

## QuerySet `with_*` methods

All expensive annotation and prefetch logic belongs in the queryset class, chainable, never
inline in a view. A new derived metric is a new `with_*` method.

`SummitQuerySet`:

| Method | Provides |
| --- | --- |
| `with_point()` | `select_related('point')` — the baseline for almost everything |
| `with_prominence()` | `prominence`, `dominance`, `distance_to_parent`; prefetches parent and key col |
| `with_distance_to_key_col()` | `distance_to_key_col` |
| `with_isolation()` | `isolation`, joins `isolation_parent__point` |
| `with_slope_parent()` | `dh`, `dd`, `slope` |
| `with_horizon_parent()` | `beta`, `angle` (refraction-free) |
| `with_ultras()` | `with_prominence()` plus boolean `ultra` (P ≥ 1500 m) |
| `with_countries()`, `with_confluence()`, `with_full_name()` | prefetches / display name |
| `with_complete()` | `has_point`, `has_key_col`, `has_prominence_parent`, `has_isolation`, `complete` |
| `only_complete()` | filtered to `complete=True` |

`ColQuerySet`: `with_point()`, `with_siblings()`, `with_minor()` (annotates `depth` and
prefetches `key_for` with prominence), `with_rivers()`, `with_countries()`, `with_full_name()`.
`RiverQuerySet`: `with_source()`, `with_parent()`, `with_tributaries()`, `with_cols()`,
`with_displacement()`, `with_direct_length()`, `with_db_status()`.

Two rules that keep these composable:

- Annotate under the same name a view will order by. `MountainListView.ORDERING` maps public
  query-string keys onto `prominence`, `isolation`, `slope`, `angle` — those names exist only
  because the corresponding `with_*` was chained in `get_queryset()`. Drop the `with_*`, and
  the ordering key silently stops working.
- `compute_*()` on the model reads an existing annotation before recomputing in Python
  (`compute_prominence()` does this via `getattr(self, 'prominence', None)`). Follow that
  pattern for new metrics so a page that annotated in SQL does not recompute per row.

Prominence appears in SQL in several places as
`Case(When(island_high_point=True, then=F('point__altitude')), default=alt − key_col_alt)`.
If you write a new one, keep the island-high-point branch — a plain subtraction yields NULL
for those summits and they vanish from ordered lists.

## Serialization and the map contract

`GeoModel` (= `GeoJsonMixin` + cairn's `AdminModel`) is the base for `Summit`, `Col`, `River`.
Each implements `to_dict()` and `to_geojson()`, where `to_geojson()` returns a Feature whose
`properties` spread in `to_dict()` and add a `type`.

`Summit.to_dict()` **is the client API.** `static/js/map.js` builds every lineage line in the
browser from those keys — `pk`, `kc` (key col id), `prominence_parent`, `isolation_parent`,
`slope_parent`, `horizon_parent`, and the `ilp` object (`name`, `dist`, `lat`, `lon`) for the
nearest higher point. It resolves parents against `/summits/geo.json` and cols against
`/cols/geo.json`, so renaming a key there breaks the maps with no Python error. Keep them.

`properties.prom` drives summit marker size *and* colour, both in `styles.js`:

- **Size** — `prominenceRadius()` scales the base radius from `SUMMIT_MIN_SCALE` (0.6) to
  `SUMMIT_MAX_SCALE` (1.75 at Everest's 8848.86 m) on a cube-root curve. The distribution is
  extremely tail-heavy (median under 200 m), so a linear ramp would pin almost everything to
  the floor and a log ramp would flatten the top. Raising the top of the range also lifts the
  middle — the exponent is the counter-knob: steepen it toward 0.5 to hold the crowd down
  while the tail grows.
- **Colour** — `prominenceBand()` buckets on the domain thresholds 1500 (ultra) / 600 / 200 /
  100 / 30, returning one of `PROMINENCE_BANDS` or `PROMINENCE_UNKNOWN`. Single-hue ordinal
  ramp validated on a light surface (monotone lightness, adjacent ΔL ≥ 0.06 — six steps fit
  at 0.063, so a seventh band means re-stepping the whole ramp, not squeezing one in — hue
  spread 0°, lightest step 3.9:1 against the surface). Note the direction is deliberately
  *inverted* against the usual convention: the lightest step is the ultra band. That only
  works because size co-encodes magnitude and the markers are ink-outlined
  (`SUMMIT_OUTLINE`) rather than white-ringed, so a big pale mark still has a defined edge.
  Beware `null >= 0 === true` in JS when
  touching these guards — the 23 summits with no prominence must land in the grey unknown
  band, not in "minor".

Colour never carries a band alone: `renderProminenceLegend()` builds the legend in
`#prominence-legend` (in `core/controls.html`) from the same `PROMINENCE_BANDS` array, and
the summit popup names the band. Add a band and both follow automatically.

Draw order is pinned with explicit z-indices, never insertion order, because layers are added
and removed as modes change. Layers (`map.js`): rivers 10, col–confluence 15, lineage 20,
key-col/isolation overlays 30, summits 40. Styles within one layer (`styles.js`): areas 10,
lines 20, points 40 — this is what keeps markers on top on the single-layer detail map.
Summits sit above everything so they stay clickable: `forEachFeatureAtPixel` returns the
topmost feature, so anything drawn over a marker steals its clicks.

`properties.type` drives styling. Point/marker types that `styles.js:styleFor()` handles:
`summit`, `prominence_parent`, `col`, `isolation_point`, `isolation_parent`,
`encirclement_parent`, `isolation_circle`, `horizon_king`. Line types it handles:
`isolation_line_first`, `isolation_line_second`, `prominence_line_first`,
`prominence_line_second`, `encirclement_line`, `slope_line`. Anything else falls through to
`default: []` and renders invisibly — which is how a broken map layer usually presents.

Client-built lineage lines do not use `type` at all; they carry a `segment` property
(`peak_to_col`, `col_to_parent`, `peak_to_nhp`, `nhp_to_parent`) consumed by
`lineageStyle(mode)`.

The map page is the client-assembled path: `isolation_map.html`'s `init_map` block calls
`initGlobalMap()`, which fetches the three flat endpoints and joins them in the browser. It
used to *also* carry an inline `<script>` building a second `ol.Map` on the same target, with
local copies of `styleFor`, `segmentStyles`, and `denseCoords` that shadowed the shared ones —
so edits to `styles.js` had no effect on that page. That block is gone. If a styling change
appears not to work, check first that no template is redefining the function.

`views.map` still serializes a whole FeatureCollection into the template context for a
`{{ geojson }}` variable nothing reads any more — dead work on every request.

Endpoints: `/summits/geo.json`, `/rivers/geo.json`, `/cols/geo.json/` (flat, via
`FlatGeoJsonView`), `/summit/<pk>/geo.json/` (assembled server-side in
`views/summit/json.py`), and the tree JSON views under `views/tree/tree.py` whose
`build_tree(summits, parent_attr)` nests `to_dict()` payloads by any `*_parent_id`.

## Views

List views subclass cairn's `OrderableListView`: declare `ORDERING` mapping public keys to
ORM paths or annotation names, override `parse_get_arguments()` to pull extra GET params
(both `MountainListView` and `col.ListView` instantiate `FilterForm` there), and build the
queryset from `with_*` chains. The public key is the security boundary — unknown keys are
dropped rather than passed to `order_by`, so never accept a raw field path from the request.

Tree views (`SummitTreeView` and its subclasses) flatten a queryset into `{parent_pk: [children]}`
in Python; subclasses supply `parent_fk` and `sort_function` as static methods. `preprocess()`
is the hook for per-object values a template needs that SQL did not annotate.

Admin actions on `SummitAdmin` are generators decorated with cairn's `@admin_action`; they
`yield` the name of each changed object for progress reporting. The three compute actions are
brute-force O(n²) loops in Python.

## Commands

```bash
uv run python manage.py runserver
uv run python manage.py test mountains
uv run python manage.py shell < .claude/skills/treeline/scripts/check_integrity.py
./db_import.sh        # DROP + recreate the DB from a dump on host `amos` (maintainer only)
```

There are no tests worth the name — `mountains/tests.py` and `core/tests.py` are the empty
Django stubs. New invariant logic is therefore best paired with a check in the integrity
script, or with the first real test case.

## Known dead and broken code

Check here before "fixing" something that is simply unwired:

- `views/__init__.py: summit_map()` and `summit_detail_map()` are not routed in `urls.py` and
  duplicate what `views/summit/json.py` does. Their references to the dropped
  `encirclement_parent` field and to `isolation_location` have been repointed at
  `compute_encirclement_parent()` and `nearest_higher_point`, so they run — but they remain
  a second implementation of the same features, and `summit_map()` emits
  `prominence_line_down` / `prominence_line_up`, which `styles.js` does not handle.
- `SummitAdmin` defines `get_queryset` twice; the first references `with_prominence_parent()`,
  `with_isolation_parent()`, `with_key_col()`, none of which exist. The second definition wins,
  so it never runs.
- `mountains/forms/namedpoint.py: NamedPointInlineForm` (and `ColAdminForm`, `SummitAdminForm`)
  is imported nowhere in the admin and reads `p.latitude` / `p.country`, which are not fields
  on `NamedPoint` (`location` and `countries` are). It cannot work as written.
- `mountains/models/range.py: Range` is not exported from `models/__init__.py` and its `parent`
  FK omits the required `on_delete`.
- `mountains/models.py` and `mountains/admin.py` are leftover Django stubs shadowed by the
  packages of the same name.
- `styles.js` references an undefined `YELLOW` constant in the `prominence_line_second` and
  `slope_line` branches; `colMarker()` has unreachable code after its `return`;
  `isolationLimitPointStyle()` passes `colour` where OpenLayers expects `color`.
