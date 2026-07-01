from cairn.views import OrderableListView
from django.db.models import F
from django.views.generic import ListView as DjangoListView, DetailView as DjangoDetailView

from mountains.models import River
from mountains.views.tree import TreeView
from mountains.views.tree.tree import FlatGeoJsonView


class ListView(OrderableListView):
    model = River
    template_name = 'mountains/river/list.html'
    context_object_name = 'rivers'

    ORDERING = {
        'name': 'source__name',
        'source_alt': 'source__altitude',
        'drop': 'drop',
    }

    def get_queryset(self):
        return River.objects.with_displacement().with_tributaries()


class DetailView(DjangoDetailView):
    model = River
    template_name = 'mountains/river/detail.html'

    def get_queryset(self):
        return River.objects.with_displacement().with_tributaries()


class RiverTreeView(TreeView):
    def get_queryset(self):
        return River.objects.with_source().with_parent()


class GeoJsonView(RiverTreeView, FlatGeoJsonView):
    object_name = 'rivers'
