"""
TreeLine data integrity report.

Run:  uv run python manage.py shell < .claude/skills/treeline/scripts/check_integrity.py

Read-only. Every check corresponds to an invariant documented in
.claude/skills/treeline/SKILL.md; ERRORs are states the domain forbids,
WARNs are states that are legal but usually indicate missing or stale data.
"""

from django.db.models import F, Q

from core.functions.world import distance
from mountains.models import Col, NamedPoint, River, Summit

errors = []
warnings = []
info = []


def err(check, obj, message):
    errors.append((check, obj, message))


def warn(check, obj, message):
    warnings.append((check, obj, message))


def label(summit):
    if summit.point is None:
        return f"Summit #{summit.pk} (no point)"
    return f"{summit.point.name or 'unnamed'} #{summit.pk}"


summits = list(
    Summit.objects.select_related(
        'point',
        'key_col__point',
        'prominence_parent__point',
        'prominence_parent__key_col__point',
        'isolation_parent__point',
        'slope_parent__point',
        'horizon_parent__point',
    )
)
by_pk = {s.pk: s for s in summits}


def prominence(summit):
    if summit.point is None:
        return None
    if summit.island_high_point:
        return summit.point.altitude
    if summit.key_col and summit.key_col.point:
        return summit.point.altitude - summit.key_col.point.altitude
    return None


# --- points -----------------------------------------------------------------

for s in summits:
    if s.point is None:
        err('no-point', s, 'summit has no NamedPoint')
        continue
    if s.point.location is None:
        err('no-location', s, 'point has no location')
    if s.point.altitude is None:
        err('no-altitude', s, 'point has no altitude')

for c in Col.objects.select_related('point'):
    if c.point is None:
        err('col-no-point', c, f'col #{c.pk} has no NamedPoint')
    elif c.point.location is None:
        err('col-no-location', c, f'col {c} has no location')

# --- prominence -------------------------------------------------------------

for s in summits:
    if s.point is None or s.point.altitude is None:
        continue

    if s.island_high_point and s.key_col_id is not None:
        err('island-with-col', s,
            f'{label(s)} is an island high point but also has key col {s.key_col}')

    if s.key_col and s.key_col.point and s.key_col.point.altitude is not None:
        if s.key_col.point.altitude >= s.point.altitude:
            err('col-not-lower', s,
                f'{label(s)} ({s.point.altitude:.0f} m) has key col '
                f'{s.key_col} at {s.key_col.point.altitude:.0f} m — must be lower')

    p = s.prominence_parent
    if p is not None and p.point is not None and p.point.altitude is not None:
        if p.point.altitude <= s.point.altitude:
            err('parent-not-higher', s,
                f'{label(s)} ({s.point.altitude:.0f} m) has prominence parent '
                f'{label(p)} ({p.point.altitude:.0f} m) — must be higher')
        mine, theirs = prominence(s), prominence(p)
        if mine is not None and theirs is not None and theirs <= mine:
            err('parent-not-more-prominent', s,
                f'{label(s)} P={mine:.0f} m but its prominence parent '
                f'{label(p)} has P={theirs:.0f} m — the parent must be more prominent')

    if s.key_col_id is not None and s.prominence_parent_id is None:
        warn('col-without-parent', s,
             f'{label(s)} has a key col but no prominence parent')
    if s.prominence_parent_id is not None and s.key_col_id is None and not s.island_high_point:
        warn('parent-without-col', s,
             f'{label(s)} has a prominence parent but no key col')

    if (prom := prominence(s)) is not None and prom < 0:
        err('negative-prominence', s, f'{label(s)} has prominence {prom:.0f} m')

# --- cycles and roots -------------------------------------------------------

for attr in ('prominence_parent_id', 'isolation_parent_id', 'slope_parent_id', 'horizon_parent_id'):
    for s in summits:
        seen = {s.pk}
        cur = by_pk.get(getattr(s, attr))
        while cur is not None:
            if cur.pk in seen:
                err(f'cycle-{attr}', s,
                    f'{label(s)} is in a cycle following {attr} (revisits {label(cur)})')
                break
            seen.add(cur.pk)
            cur = by_pk.get(getattr(cur, attr))

roots = [s for s in summits if s.prominence_parent_id is None and not s.island_high_point]
if len(roots) > 1:
    names = ', '.join(sorted(label(s) for s in roots))
    warn('multiple-prominence-roots', None,
         f'{len(roots)} summits have no prominence parent and are not island high points: {names}')

# --- isolation --------------------------------------------------------------

for s in summits:
    if s.point is None or s.point.location is None:
        continue

    has_nhp = s.nearest_higher_point is not None
    has_parent = s.isolation_parent_id is not None

    if has_parent and not has_nhp:
        err('parent-without-nhp', s,
            f'{label(s)} has isolation parent {label(s.isolation_parent)} '
            f'but no nearest_higher_point')
    if has_nhp and not has_parent:
        warn('nhp-without-parent', s,
             f'{label(s)} has a nearest_higher_point but no isolation parent')

    ip = s.isolation_parent
    if ip is not None and ip.point is not None and ip.point.altitude is not None:
        if ip.point.altitude <= s.point.altitude:
            err('isolation-parent-not-higher', s,
                f'{label(s)} ({s.point.altitude:.0f} m) has isolation parent '
                f'{label(ip)} ({ip.point.altitude:.0f} m) — must be higher')

    if has_nhp and ip is not None and ip.point and ip.point.location:
        to_nhp = distance(
            (s.point.location.y, s.point.location.x),
            (s.nearest_higher_point.y, s.nearest_higher_point.x),
        ).km
        to_parent = distance(
            (s.point.location.y, s.point.location.x),
            (ip.point.location.y, ip.point.location.x),
        ).km
        # The nearest higher ground normally lies between the summit and the massif
        # of its isolation parent, so it should not be farther away than the parent.
        if to_nhp > to_parent + 0.5:
            warn('nhp-beyond-parent', s,
                 f'{label(s)}: nearest_higher_point is {to_nhp:.1f} km away but its '
                 f'isolation parent {label(ip)} is only {to_parent:.1f} km away')
        # A nearest higher point on top of the parent summit usually means nobody has
        # located the actual nearest higher ground yet.
        offset = distance(
            (ip.point.location.y, ip.point.location.x),
            (s.nearest_higher_point.y, s.nearest_higher_point.x),
        ).m
        if offset < 1:
            warn('nhp-equals-parent', s,
                 f'{label(s)}: nearest_higher_point coincides with the summit of '
                 f'{label(ip)} — isolation is probably an unrefined peak-to-peak distance')

    if s.isolation_name and not has_parent:
        warn('isolation-name-orphan', s,
             f'{label(s)} has isolation_name "{s.isolation_name}" but no isolation parent')

# --- suspicious coordinates -------------------------------------------------

for s in summits:
    if s.point is None or s.point.location is None:
        continue
    lat, lon = s.point.location.y, s.point.location.x
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        err('bad-coordinates', s, f'{label(s)} at lat={lat} lon={lon}')
    elif abs(lat) < 0.001 and abs(lon) < 0.001:
        err('null-island', s, f'{label(s)} sits at 0°/0° — coordinates never entered?')
    if s.point.altitude is not None and not (-500 < s.point.altitude < 9000):
        warn('implausible-altitude', s, f'{label(s)} altitude {s.point.altitude} m')

# A summit and its key col at the same spot means one of them is misplaced. Rock towers
# in the High Tatras genuinely have their notch 15–50 m away, so the threshold is tight.
for s in summits:
    if s.point and s.point.location and s.key_col and s.key_col.point and s.key_col.point.location:
        d = distance(
            (s.point.location.y, s.point.location.x),
            (s.key_col.point.location.y, s.key_col.point.location.x),
        ).m
        if d < 10:
            warn('col-on-summit', s,
                 f'{label(s)} and its key col {s.key_col} are {d:.0f} m apart')

# --- cols and hydrology -----------------------------------------------------

orphan_cols = Col.objects.filter(key_for__isnull=True).count()
if orphan_cols:
    info.append(f'{orphan_cols} cols are not the key col of any summit')

no_hydrology = Col.objects.filter(confluence__isnull=True, confluence_river__isnull=True).count()
if no_hydrology:
    info.append(f'{no_hydrology} cols have neither confluence nor confluence_river')

for r in River.objects.select_related('source', 'parent__source'):
    if r.parent and r.mouth_altitude is not None and r.parent.mouth_altitude is not None:
        if r.mouth_altitude < r.parent.mouth_altitude:
            err('river-mouth-below-parent', r,
                f'{r.name()} mouth {r.mouth_altitude:.0f} m is below the mouth of its '
                f'parent {r.parent.name()} ({r.parent.mouth_altitude:.0f} m)')
    if r.parent_id == r.pk:
        err('river-self-parent', r, f'{r.name()} is its own parent')

# --- duplicates -------------------------------------------------------------

# Pairs that are legitimately close: a summit and its own key col.
allowed_pairs = {
    frozenset((s.point_id, s.key_col.point_id))
    for s in summits
    if s.point_id and s.key_col and s.key_col.point_id
}

located = [p for p in NamedPoint.objects.all() if p.location is not None]
located.sort(key=lambda p: (round(p.location.y, 2), round(p.location.x, 2)))
for a, b in zip(located, located[1:]):
    if abs(a.location.y - b.location.y) > 0.01:
        continue
    if frozenset((a.pk, b.pk)) in allowed_pairs:
        continue
    d = distance(
        (a.location.y, a.location.x),
        (b.location.y, b.location.x),
    ).m
    if d < 30:
        warn('near-duplicate-points', a,
             f'"{a}" and "{b}" are {d:.0f} m apart — possible duplicate')

# --- report -----------------------------------------------------------------

total = Summit.objects.count()
complete = Summit.objects.only_complete().count()

print()
print(f'{total} summits, {complete} complete ({complete / total:.0%}), '
      f'{Col.objects.count()} cols, {River.objects.count()} rivers')
print()

for name, bucket in (('ERROR', errors), ('WARN', warnings)):
    if not bucket:
        continue
    print(f'{name}S ({len(bucket)})')
    by_check = {}
    for check, obj, message in bucket:
        by_check.setdefault(check, []).append(message)
    for check in sorted(by_check):
        messages = by_check[check]
        print(f'  {check} ({len(messages)})')
        for message in messages[:15]:
            print(f'    - {message}')
        if len(messages) > 15:
            print(f'    … and {len(messages) - 15} more')
    print()

for line in info:
    print(f'INFO  {line}')

if not errors:
    print('No invariant violations.')
