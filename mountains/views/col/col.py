from django.db.models import F, Prefetch, Q, prefetch_related_objects
from django.views import View
from django.views.generic import DetailView as DjangoDetailView, ListView as DjangoListView

from ..tree.tree import CachedJsonMixin, TreeView
from ..viewport import ViewportDetailView
from ...models import Col, River, Summit
from ..detail_map import col_features
from ..drainage import drainage_context, drainage_runs      # noqa: F401 — drainage_runs re-exported


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
                .prefetch_related('point__countries', 'point__notes', 'point__names',
                                  # The local names in the popup titles of the two rivers.
                                  'confluence_river__source__names',
                                  'confluence_river__parent__source__names'))

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
        drainage = drainage_context(self.object, downstream=True)
        context['drainage'] = drainage
        context['map_data'] = col_features(self.object, runs=drainage['runs'],
                                          downstream=drainage['downstream'])
        return context


class ColTreeView(TreeView):
    def get_queryset(self):
        # `point__names`: the popup title prints the col's local names.
        return (Col.objects.with_rivers().with_minor().with_point().with_countries()
                .prefetch_related('point__names'))


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

    def rank_queryset(self):
        return Col.objects.annotate(depth=F('key_for__point__altitude') - F('point__altitude'))
