from django.db.models import F, Q
from django.views import View
from django.views.generic import DetailView as DjangoDetailView, ListView as DjangoListView

from ..tree.tree import CachedJsonMixin, TreeView
from ..viewport import ViewportDetailView
from ...models import Col


class DetailView(DjangoDetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col/detail.html'

    def get_queryset(self):
        return super().get_queryset().with_full_name().with_minor().select_related('point')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context |= {
            'friend_cols': Col.objects.filter(
                Q(confluence_river=self.object.confluence_river) & Q(confluence_river__isnull=False)
            )
        }
        return context


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
