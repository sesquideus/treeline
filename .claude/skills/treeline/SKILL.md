---
name: treeline
description: Domain rules and code patterns for TreeLine, the GeoDjango database of the world's peaks — summits, cols, prominence, isolation, and the slope/horizon parent hierarchies. Use when adding or correcting peak data, checking data integrity, writing queries over summits/cols/rivers, or extending the Django app (querysets, views, GeoJSON, admin).
---

# TreeLine

TreeLine aims to be the authoritative relational record of the world's peaks: each summit's
position and altitude, its key col, and the derived topographic relations — prominence,
isolation, and the slope and horizon parent hierarchies. Data quality is the product. A
plausible-looking number that nobody verified is worse than a null.

Two halves, use whichever the task needs:

- **Data** — the invariants below, `references/domain.md` for what the metrics mean.
- **Code** — `references/codebase.md` for querysets, views, serialization, admin.

Current scale (re-check with the stats recipe; it drifts): ~500 summits, ~490 cols, ~130
rivers, ~85% of summits complete. Coverage is dense in the Western Carpathians and sparse
but growing elsewhere — mostly world ultras and Alpine peaks.

## The rules that break things when ignored

**Coordinate order.** A GeoDjango `Point` is `Point(lon, lat)`. `location.y` is latitude,
`location.x` is longitude. The helpers in `core/functions/world.py` (`distance`, `azimuth`)
take `(lat, lon)` tuples — the opposite order. This is the single most common bug in the
repo; check it every time you touch coordinates.

**Everything geographic is a `NamedPoint`.** `Summit` and `Col` do not have a name, a
location, or an altitude — they wrap a `NamedPoint` through a `OneToOneField` named `point`.
Read `summit.point.altitude`, never `summit.altitude`. Almost every query needs
`select_related('point')` or it becomes an N+1.

**`save()` does not validate.** The invariants live in `Summit.clean()` and `River.clean()`,
which Django runs from ModelForms and the admin — *not* from `Model.save()`. When writing
data from a shell or a script, call `obj.full_clean()` yourself before saving, or you will
persist a state the domain forbids.

**Altitudes are metres, floats.** Distances returned by queryset annotations and by the
`core.functions.world.distance` helper are distance objects — use `.m` / `.km`, never the
bare value.

## Invariants

Errors — the data model forbids these. Enforced in `Summit.clean()` / `River.clean()` and
checked by `scripts/check_integrity.py`.

| Invariant | Why |
| --- | --- |
| `key_col.point.altitude < point.altitude` | A summit cannot be lower than its own saddle. |
| `prominence_parent.point.altitude > point.altitude` | The parent is higher terrain by definition. |
| `prominence_parent` prominence > own prominence | Prominence parentage flows toward more prominent peaks; equal or lower breaks the tree's meaning. |
| No cycle in `prominence_parent` (nor slope/horizon/isolation) | Each is a forest, not a graph. |
| `isolation_parent.point.altitude > point.altitude` | The isolation parent owns *higher* ground. |
| `isolation_parent` set ⇒ `nearest_higher_point` set | The parent is an attribution of the ground; the ground itself is the measurement. |
| `island_high_point` ⇒ no `key_col` | Its key col is the sea; prominence equals altitude. |
| `river.mouth_altitude >= parent.mouth_altitude` | Water does not flow uphill into its receiving stream. |

Warnings — legal states that almost always mean unfinished curation:

- `nearest_higher_point` set but no `isolation_parent` (the ground was located, the peak it
  belongs to was never attributed). The largest backlog in the DB today.
- `isolation_name` set but no `isolation_parent`.
- `key_col` set but no `prominence_parent`, or the reverse.
- `nearest_higher_point` exactly on the isolation parent's summit — that is a peak-to-peak
  distance standing in for a real isolation measurement, and it overstates isolation.
- A summit with no `prominence_parent` that is not an `island_high_point`. In a complete
  dataset the only roots of the prominence forest are the island high points (Everest
  included — Afro-Eurasia is an island); every other root is an unlinked subtree.

## Checking the data

```bash
uv run python manage.py shell < .claude/skills/treeline/scripts/check_integrity.py
```

Read-only, takes a few seconds, groups findings by check. Run it after any batch of edits
and after `./db_import.sh`. Errors are violations; warnings are the curation backlog. If a
warning class turns out to be a legitimate modelling pattern rather than a defect, remove
the check rather than living with the noise — a report nobody trusts is not a report.

## Adding or correcting a peak

The app has no importer and no management commands: data entry is the Django admin
(`/admin/mountains/`), and bulk work is `manage.py shell`. Either way the order is the same,
because each step depends on the previous one existing.

1. **`NamedPoint`** — name (unique, nullable for unnamed points), `location`, `altitude`,
   `countries`, `source`. Record the `Source`; provenance is what makes the record
   authoritative. Multilingual names go in `PointName` rows, not in `name`.
2. **`Col`** — its own `NamedPoint`. Cols are frequently unnamed; leave `point.name` null
   rather than inventing one, `Col.__str__` falls back to `unnamed → <summit>`.
3. **`Summit`** — link `point`, then `key_col` and `prominence_parent` together. Setting one
   without the other leaves the record half-connected.
4. **Isolation** — `nearest_higher_point` is the actual nearest higher *ground*, not the
   parent summit. `isolation_parent` attributes that ground to a summit, `isolation_name`
   describes it ("northern ridge of Kopa").
5. **Slope and horizon parents** — never by hand. Admin actions on the summit changelist:
   *Compute slope*, *Compute horizon parent*, *Compute horizon parent (std)*. They are
   O(n²) Python loops over every summit; fine at current scale, slow at 10× it.
6. **Verify** — run the integrity script.

From the shell, validate explicitly:

```python
from django.contrib.gis.geos import Point
from mountains.models import NamedPoint, Col, Summit, Source

src = Source.objects.get(name='...')
p = NamedPoint(name='Ostrý Roháč',                       # NamedPoint.name is globally unique
               location=Point(19.7361, 49.2069, srid=4326),   # Point(lon, lat)
               altitude=2088.0, source=src)
p.full_clean(); p.save()

s = Summit(point=p, key_col=Col.objects.get(pk=...),
           prominence_parent=Summit.objects.get(point__name='...'))
s.full_clean()   # runs the invariant checks — do not skip
s.save()
```

Inserting a summit *between* an existing peak and its parent means re-pointing the child's
`prominence_parent` too; nothing cascades that for you, and the "parent must be more
prominent" check will catch it only on the record you happen to validate.

## Query recipes

Compose the `with_*` queryset methods; do not hand-write the annotations (see
`references/codebase.md`).

```python
# stats
Summit.objects.count(), Summit.objects.only_complete().count()

# ultras (P >= 1500 m), most prominent first
Summit.objects.with_ultras().filter(ultra=True).order_by('-prominence')

# the isolation backlog: ground located, parent unattributed
Summit.objects.filter(nearest_higher_point__isnull=False, isolation_parent__isnull=True) \
              .select_related('point')

# what is missing on a record
Summit.objects.with_complete().filter(complete=False) \
    .values('point__name', 'has_point', 'has_key_col', 'has_prominence_parent', 'has_isolation')

# nearest summits to a point, by real geodesic distance
from django.contrib.gis.db.models.functions import Distance
Summit.objects.with_point().annotate(d=Distance('point__location', pt)).order_by('d')[:10]

# candidate key cols for a summit: lower than it, sorted by height
Col.objects.with_point().filter(point__altitude__lt=alt).order_by('-point__altitude')
```

`with_complete()` requires a key col **or** the `island_high_point` flag, since the sea is an
island high point's col. It still requires a prominence parent and isolation, so Mount
Everest — with nothing higher on Earth to parent it — can never report complete.

`Summit.objects.with_prominence()` annotates `prominence`, `dominance`, and
`distance_to_parent`; `with_isolation()` annotates `isolation`; `with_slope_parent()`
annotates `slope`; `with_horizon_parent()` annotates `angle`. Ordering and filtering
reference those names.

## Extending the app

Read `references/codebase.md` before adding a metric, a view, or a map layer. In short:
new derived metrics become `with_*` queryset methods, map features get a
`properties.type` that must already exist in `static/js/styles.js`, views live in the
per-model packages under `mountains/views/` and are re-exported through `__init__.py`.

`references/codebase.md` also lists the known dead and broken code in the tree — check it
before "fixing" something that is simply no longer wired up.
