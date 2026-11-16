from django.db.models import F, Prefetch, Q, prefetch_related_objects
from django.views import View
from django.views.generic import DetailView as DjangoDetailView, ListView as DjangoListView

from ..tree.tree import CachedJsonMixin, TreeView
from ..viewport import ViewportDetailView
from ...models import Col, River, Summit
from ..detail_map import col_features


class DetailView(DjangoDetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col/detail.html'

    #: Everything on the page that is one object per col, joined into the one query: the
    #: summit it is the key col for, that summit's parent with the parent's own key col (its
    #: prominence is on the map), and both rivers below with what their popups name.
    JOINED = ('point', 'point__altitude_datum', 'key_for__point', 'key_for__prominence_parent__point',
              'key_for__prominence_parent__key_col__point',
              *(f'{river}__{relation}'
                for river in ('confluence_river', 'confluence_river__parent')
                for relation in ('source', 'mouth', 'source_summit__point',
                                 'watershed_high_point__summit', 'parent__source')))

    def get_queryset(self):
        return (super().get_queryset().with_full_name()
                .annotate(depth=F('key_for__point__altitude') - F('point__altitude'))
                .select_related(*self.JOINED)
                .prefetch_related('point__countries', 'point__notes'))

    def get_object(self, queryset=None):
        col = super().get_object(queryset)
        # A river's course runs through its junctions: the mouths of its tributaries and the
        # sources of its branches. One query each for both rivers together, not per river.
        rivers = [r for r in (col.confluence_river,
                              col.confluence_river.parent if col.confluence_river else None) if r]
        prefetch_related_objects(
            rivers,
            Prefetch('tributaries', queryset=River.objects.select_related('mouth')),
            Prefetch('branches', queryset=River.objects.select_related('source')))
        return col

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lower = getattr(self.object, 'key_for', None)
        # Both tables print, per row, a col and the summits on either side of it, all by name.
        # Read through the bare relations, as they used to be, that was a query per name: a
        # col draining to the sea has a hundred-odd friends, and the page ran to 750 queries.
        context |= {
            'siblings': (Summit.objects
                         .filter(prominence_parent=lower.prominence_parent_id)
                         .select_related('point', 'key_col__point')
                         # The pk breaks ties in altitude, which are common enough, and which
                         # Postgres otherwise returns in whatever order the plan gives.
                         .order_by('-point__altitude', 'pk')
                         if lower is not None and lower.prominence_parent_id else Summit.objects.none()),
            'friend_cols': Col.objects.filter(
                Q(confluence_river=self.object.confluence_river) & Q(confluence_river__isnull=False)
            ).select_related('point', 'key_for__point', 'key_for__prominence_parent__point')
             .order_by('-point__altitude', 'pk'),
        }
        drainage = drainage_context(self.object)
        context['drainage'] = drainage
        context['map_data'] = col_features(self.object, runs=drainage['runs'])
        return context


def drainage_context(col):
    """
    `col.drainage()` with the river pks swapped for rivers to link to: the whole river tree
    in one query of plain values for the analysis, the few rivers shown in one more.
    """
    drainage = col.drainage()
    wanted = {pk for side in drainage.sides for pk in side.chain}
    wanted |= {pk for pk in (drainage.meet_river, drainage.meet_into) if pk is not None}
    # The confluence and its parent came with the col; only the rest needs fetching.
    rivers = {river.pk: river for river in (col.confluence_river,
                                            col.confluence_river.parent if col.confluence_river else None)
              if river is not None}
    if wanted - rivers.keys():
        rivers |= River.objects.select_related('source', 'mouth').in_bulk(wanted - rivers.keys())
    runs = drainage_runs(drainage, rivers)
    # Their junctions, for the map: one query each for all of them, as for the confluence.
    fresh = [river for river, _, _ in runs if river not in (col.confluence_river,
                                                            getattr(col.confluence_river, 'parent', None))]
    prefetch_related_objects(
        fresh,
        Prefetch('tributaries', queryset=River.objects.select_related('mouth')),
        Prefetch('branches', queryset=River.objects.select_related('source')))
    return {
        'runs': runs,
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


class ColTreeView(TreeView):
    def get_queryset(self):
        return Col.objects.with_rivers().with_minor().with_point().with_countries()


class GeoJsonView(CachedJsonMixin, View):
    """
    Every col, as the skeleton the global map draws from: its position and the position of
    its confluence, which the pink line runs to. Popup detail comes from `DetailJsonView`.
    """

    def build_payload(self):
        return {
            'type': 'FeatureCollection',
            'features': Col.objects.skeleton_features(),
        }


class DetailJsonView(ColTreeView, ViewportDetailView):
    """
    Popup detail for the cols in a viewport — the deepest first, `depth` being the
    annotation `ColTreeView`'s queryset already carries, along with the rivers and countries
    a col popup shows.
    """
    ordering = (F('depth').desc(nulls_last=True),)


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

