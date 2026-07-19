from django.db.models import Q
from django.views.generic import DetailView as DjangoDetailView, ListView as DjangoListView

from ..tree.tree import FlatGeoJsonView, TreeView
from ...models import Col


class DetailView(DjangoDetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col/detail.html'

    def get_queryset(self):
        return super().get_queryset().with_full_name().select_related('point')

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
        return Col.objects.with_rivers().with_minor()


class GeoJsonView(ColTreeView, FlatGeoJsonView):
    object_name = 'cols'