from cairn.views import OrderableListView
from django.views.generic import DetailView as DjangoDetailView

from ..tree.tree import FlatGeoJsonView
from ...models import Range


class ListView(OrderableListView):
    """
    Every range in every system, as a tree.

    No `DEFAULT_ORDERING`: `Range.Meta.ordering` is ('system', 'path'), which — with the C
    collation on `path` — walks each system's tree depth-first with every subtree contiguous.
    That is the useful default, and ordering by anything else deliberately flattens it.
    """
    model = Range
    context_object_name = 'ranges'
    template_name = 'mountains/range/list.html'

    ORDERING = {
        'name': ('name', {'ci': True}),
        'system': 'system__code',
        'level': ('level_name', {'nulls': 'last'}),
        'summits': 'direct_summit_count',
    }

    def get_queryset(self):
        self.queryset = (Range.objects
                         .with_system()
                         .with_parent()
                         .with_direct_summit_count())
        return super().get_queryset()


class DetailView(DjangoDetailView):
    model = Range
    context_object_name = 'range'
    template_name = 'mountains/range/detail.html'

    def get_queryset(self):
        return super().get_queryset().with_system().with_parent()

    def get_context_data(self, **kwargs):
        range_ = self.object
        return super().get_context_data(**kwargs) | {
            # `ancestors()` reads the path, so the breadcrumb is one query however deep.
            'ancestors': range_.ancestors(),
            'children': range_.children.with_direct_summit_count().order_by('path'),
            # Everything at or below, which is what a reader means by "peaks in the Tatras";
            # `memberships` alone would be only those pinned exactly at this node.
            'summits': (range_.all_summits()
                        .with_point().with_prominence()
                        .order_by('-point__altitude')),
            'high_point': range_.high_point(),
        }


class GeoJsonView(FlatGeoJsonView):
    """
    The polygons, for the global map. Ranges with no `area` serialize to None and are
    dropped by `FlatGeoJsonView`, so a nominal-only hierarchy yields an empty collection
    rather than an error — filtered here as well so they are not loaded at all.
    """
    def get_queryset(self):
        return Range.objects.with_system().exclude(area__isnull=True)
