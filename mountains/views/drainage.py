"""
A col's drainage for a page: `Col.drainage()` (mountains/models/drainage.py) with rivers to
link to and the stretches to draw. Used by the col's page and by the map of the summit the
col is the key col of.
"""
from django.db.models import Prefetch, prefetch_related_objects

from mountains.models import River


def drainage_context(col, downstream=False):
    """
    `col.drainage()` with the river pks swapped for rivers to link to: the whole river tree
    in one query of plain values for the analysis, the few rivers shown in one more.
    With `downstream`, also where the joined water goes from where it meets, down to the
    sea, for `detail_map.downstream_from()`.
    """
    from mountains.models.drainage import RiverIndex
    index = RiverIndex() if downstream or col.river_a_id or col.river_b_id else None
    drainage = col.drainage(index)
    wanted = {pk for side in drainage.sides for pk in side.chain}
    wanted |= {pk for pk in (drainage.meet_river, drainage.meet_into) if pk is not None}
    # With only a confluence, its two rivers came with the col. With sides, all of them in
    # one query with what their popups read — the confluence's too, since a summit's map
    # loads its key col with less.
    rivers = {river.pk: river for river in (col.confluence_river,
                                            col.confluence_river.parent if col.confluence_river else None)
              if river is not None}
    if drainage.sides:
        from mountains.views.detail_map import RIVER_POPUP
        rivers = (River.objects.select_related(*RIVER_POPUP)
                  .prefetch_related('source__names', 'mouth__names').in_bulk(wanted))
    runs = drainage_runs(drainage, rivers)
    # Their junctions, for the map — the confluence river and its parent drawn whole, the
    # runs cut between them: one query each for all those not loaded already.
    drawn = [river for river, _, _ in runs] + [rivers.get(drainage.meet_river),
                                               rivers.get(drainage.meet_into)]
    fresh = list({river.pk: river for river in drawn if river is not None
                  and 'tributaries' not in getattr(river, '_prefetched_objects_cache', {})}.values())
    if fresh:
        prefetch_related_objects(
            fresh,
            Prefetch('tributaries', queryset=River.objects.select_related('mouth')),
            Prefetch('branches', queryset=River.objects.select_related('source')))
    return {
        'runs': runs,
        'downstream': below_meeting(index, drainage, runs) if downstream else [],
        # Each side's rivers above where the waters meet; the meeting river itself is shown
        # once, in the junction row under both columns.
        # Each river with the one above it that flows into it, whose bank mark joins them.
        'sides': [{'role': side.role, 'end': side.end,
                   'rivers': [{'river': rivers[pk],
                               'joined_by': rivers[side.chain[i - 1]] if i else None}
                              for i, pk in enumerate(side.chain[:-1] if side.end == 'target'
                                                     else side.chain)],
                   'into_meet': rivers[side.chain[-2]] if side.end == 'target'
                                and len(side.chain) > 1 else None}
                  for side in drainage.sides],
        'meet': drainage.meet,
        'meet_river': rivers.get(drainage.meet_river),
        'meet_into': rivers.get(drainage.meet_into),
        'findings': drainage.findings,
    }


def drainage_runs(drainage, rivers):
    """
    The stretch of each river a drop runs along, as (river, enter, leave): point pks for
    `River.course()`, None for an open end. A river is entered at the mouth of the one above
    it in its side's chain — the first at its source, as near as the catalogue gets — and
    left at its mouth; except where the two waters meet:

    - at a confluence X → P, the main side leaves P at the mouth of X;
    - on a common river C (no catalogued confluence), C runs between the two sides' entries,
      from the higher to the lower, where they meet;

    and a river the water only reaches at a point nobody catalogued — a side that *is* the
    confluence parent or the common river — has no stretch to draw.
    """
    runs = []
    meet_river = rivers.get(drainage.meet_river)
    for side in drainage.sides:
        chain = [rivers[pk] for pk in side.chain]
        for i, river in enumerate(chain):
            enter = chain[i - 1].mouth_id if i else None
            leave = river.mouth_id
            last = i == len(chain) - 1
            if last and drainage.meet == 'mouth' and side.role == 'main':
                if i == 0:
                    continue
                leave = meet_river.mouth_id if meet_river else None
            elif last and drainage.meet == 'on' and side.end == 'target':
                continue                # the common river: drawn once, below
            runs.append((river, enter, leave))
    if drainage.meet == 'on' and meet_river is not None:
        entries = [rivers[side.chain[-2]].mouth for side in drainage.sides if len(side.chain) > 1]
        entries = [point for point in entries if point is not None and point.altitude is not None]
        if len(entries) == 2:
            high, low = sorted(entries, key=lambda point: point.altitude, reverse=True)
            runs.append((meet_river, high.pk, low.pk))
    return runs


def below_meeting(index, drainage, runs):
    """
    Where the joined water flows on: at a confluence X → P, P from the mouth of X; on a
    common river C, C from the lower of the two sides' entries (from its source when that is
    not known, as for a spur); then everything below, to the root.
    """
    from mountains.views.detail_map import downstream_from
    if drainage.meet == 'mouth' and drainage.meet_into is not None:
        return downstream_from(index, drainage.meet_into, index.mouth.get(drainage.meet_river))
    if drainage.meet == 'on' and drainage.meet_river is not None:
        lowest = next((leave for river, enter, leave in runs if river.pk == drainage.meet_river), None)
        return downstream_from(index, drainage.meet_river, lowest)
    return []
