from cairn.views import OrderableListView
from django.db.models import Q, F
from django.views.generic import DetailView as DjangoDetailView, ListView as DjangoListView

from .tree.tree import FlatGeoJsonView, TreeView
from ..models import Col


class DetailView(DjangoDetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col/detail.html'

    def get_queryset(self):
        return super().get_queryset().with_full_name().select_related('point')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context |= {
            'friend_cols': Col.objects.filter(Q(confluence_river=self.object.confluence_river) & Q(confluence_river__isnull=False))
        }
        return context


class ListView(OrderableListView):
    model = Col
    context_object_name = 'cols'
    template_name = 'mountains/col/list.html'

    ORDERING = {
        'name': 'point__name',
        'altitude': 'point__altitude',
        'minor-name': 'key_for__point__name',
        'minor-alt': 'key_for__point__altitude',
        'prominence': 'prominence',
        'major-name': 'key_for__prominence_parent__point__name',
        'major-alt': 'key_for__prominence_parent__point__altitude',
        'river-name': 'confluence_river__source__name',
        'river-alt': 'confluence_river__source__altitude',
        'confluence-alt': 'confluence_river__mouth_altitude',
    }

    def get_queryset(self):
        qs = Col.objects.with_point().with_minor().with_countries().with_river()

        if self.ordering:
            if self.ordering[0] == '-':
                ordering = self.ordering[1:]
                qs = qs.order_by(F(ordering).desc(nulls_last=True))
            else:
                ordering = self.ordering
                qs = qs.order_by(F(ordering).asc(nulls_last=True))
        else:
            qs = qs.order_by('-point__altitude')

        return qs


class ColTreeView(TreeView):
    def get_queryset(self):
        return Col.objects.with_river().with_minor()


class GeoJsonView(ColTreeView, FlatGeoJsonView):
    object_name = 'cols'