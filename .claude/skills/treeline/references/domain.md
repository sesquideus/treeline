# The metrics

What each stored field means, how it is computed, and where it is easy to get wrong.

## Prominence

The height of a summit above the highest col that connects it to any higher terrain. That
col is the **key col** (`Summit.key_col`), and

```
prominence = point.altitude − key_col.point.altitude
```

Equivalently: how far you must descend before you can climb something higher. It is the
measure of how much of a mountain a mountain is — Mont Blanc, 4807.3 m with its key col at
118 m near Lake Kubenskoye, has 4689 m of prominence, while a 4000 m shoulder of a
neighbouring peak may have 30 m.

**Island high points.** For the high point of an island or continent the key col is sea
level, so `prominence = altitude`. TreeLine models this with the `island_high_point` flag
rather than a fake sea-level col; every prominence expression in the codebase is a
`Case(When(island_high_point=True, then=F('point__altitude')), default=alt − col_alt)`.
Set the flag *instead of* a key col, never alongside one.

**Dominance** = `prominence / altitude`, annotated by `with_prominence()`. Ranges 0–1; 1.0
for an island high point.

**Ultra** = prominence ≥ 1500 m. Annotated by `with_ultras()` as a boolean `ultra`.

### The prominence parent

`prominence_parent` is the higher, *more prominent* peak that this summit's key col leads
to. The two constraints — higher altitude **and** greater prominence — are what make
`prominence_parent` a well-founded forest, and both are enforced in `Summit.clean()`.

Be aware that the peakbagging literature has several competing parent definitions and they
disagree in ordinary cases:

- **Line parent** — the first higher peak along the ridge from the key col. Not what
  TreeLine stores.
- **Prominence parent** — the nearest peak with greater prominence, walking from the key
  col. This is TreeLine's `prominence_parent`.
- **Encirclement parent** — the closest peak whose territory (the region bounded by the
  contour at this peak's key col level) encloses this one. Deliberately *not* stored: it
  is fully determined by the prominence chain, so keeping a column for it would be a
  denormalization that can go stale. It was once a field and was dropped in migration
  `0021_remove_summit_encirclement_parent`. `Summit.compute_encirclement_parent()` derives
  it by walking up `prominence_parent` to the first peak with a lower key col; the source
  still marks that implementation unverified. It surfaces on the summit detail map
  (`encirclement_parent` / `encirclement_line` features) and as the ◎ marker in the
  prominence lineage table.

When importing prominence parentage from an external source, check which definition that
source uses before trusting the values.

## Isolation

The distance from a summit to the nearest point of ground at least as high — not to the
nearest higher *summit*. TreeLine stores both:

- `nearest_higher_point` — a raw `PointField` holding that ground. This is the measurement.
  `isolation = distance(point.location, nearest_higher_point)`.
- `isolation_parent` — the summit that piece of ground belongs to. An attribution, useful
  for building the isolation forest, but not part of the distance.
- `isolation_name` — free text describing the ground ("northern ridge of Kopa",
  "southwestern slope"), because it usually has no name of its own.

There is deliberately no altitude on `nearest_higher_point`: by definition it sits at this
summit's own altitude, on the contour where higher terrain begins.

The frequent shortcut — storing the parent summit's coordinates as `nearest_higher_point` —
produces a peak-to-peak distance, which always overstates isolation, sometimes by a lot.
The integrity script flags it (`nhp-equals-parent`).

Helper methods on `Summit`, all returning `{'az': degrees, 'dist': metres}`:

| Method | Vector |
| --- | --- |
| `isolation_vector()` | summit → nearest higher ground (this is isolation) |
| `isolation_vector_p2p()` | summit → isolation parent summit |
| `isolation_offset_vector()` | isolation parent summit → the nearest higher ground |

`compute_isolation()` uses geopy's WGS84 geodesic; the `with_isolation()` annotation uses
PostGIS `ST_Distance` on geography columns. Both are ellipsoidal and agree to centimetres,
but they return different objects — a geopy distance and a `django.contrib.gis.measure.D`.
Both expose `.m` and `.km`; neither is a plain number.

## Slope parent

The summit maximizing the ratio of elevation gain to distance:

```
slope = (other.altitude − this.altitude) / distance(this, other)   # metres / metres
```

`NamedPoint.slope_to(other)` computes it for a pair; `with_slope_parent()` annotates `dh`,
`dd`, and `slope` for the stored parent. The stored `slope_parent` is found by the admin
action *Compute slope*, which is a brute-force max over every summit in the database — so
it is only as good as the database's coverage, and it changes as peaks are added. Mount
Everest is skipped explicitly (nothing is higher).

## Horizon parent

The summit that stands highest above this summit's horizon, accounting for the curvature
of the Earth. `NamedPoint.angle_to(other, refraction=0.0)`:

```
r    = 6_371_000 / (1 − refraction)
β    = distance / r                       # angular separation
angle = atan( ((r + h₂)·cos β − (r + h₁)) / ((r + h₂)·sin β) )
```

A positive angle means the other summit is above the horizontal — visible over the bulge,
geometry permitting. Two parents are stored:

- `horizon_parent` — purely geometric, `refraction = 0`.
- `horizon_parent_std` — with the standard atmospheric refraction coefficient k = 0.14,
  which bends sightlines down over the horizon and is modelled as an effective Earth radius
  of `R / (1 − k)` ≈ 7409 km. This is the one to compare against real-world visibility
  reports.

Both come from admin actions, both brute-force over all summits. The `with_horizon_parent()`
queryset annotation (`beta`, `angle`) reproduces the **refraction-free** formula in SQL for
`horizon_parent` only; there is no annotation for the `_std` variant, so anything that needs
it must go through `angle_to(..., refraction=0.14)` in Python.

## Cols, rivers, confluences

A `Col` is a `NamedPoint` plus optional hydrology. Its reverse `key_for` (from
`Summit.key_col`, a OneToOne) is the summit it is the key col of — a col may be the key col
of at most one summit, and a handful currently belong to none.

The hydrological link is what makes a col meaningful beyond topography: a col separates two
drainage basins, so it corresponds to a point in the river network. Either field may be set,
and both together is a normal combination:

- `confluence_river` — the river the col drains toward.
- `confluence` — an explicit `Confluence` point.

`River` forms its own tree via `parent` (the receiving river) with `tributaries` as the
reverse. A river's `source` is a `NamedPoint`; its `mouth` is a raw `PointField` with a
separate `mouth_altitude`, and a mouth may not be lower than its parent's mouth.
`get_waypoints()` builds a polyline as source → tributary mouths (descending by altitude) →
mouth, which is the geometry that reaches `to_geojson()`.

`branches_off` marks a river whose source is a bifurcation from another river rather than a
spring.

## Provenance

`Source` (name + integer `quality`, lower sorts first) is attachable to `NamedPoint`,
`PointName`, and separately to a summit's prominence (`prominence_source`) and isolation
(`isolation_source`) — because those three facts routinely come from three different
places. Recording where a number came from is what separates this database from a
spreadsheet of guesses; fill it in.

`PointName` holds names per `Language`, with a `local` flag for the endonym. The single
`NamedPoint.name` is the working label and is globally unique — which means two genuinely
distinct peaks sharing a name need a disambiguator, as with `Kľak Veľkofatranský` and
`Rysy (polski)`.
