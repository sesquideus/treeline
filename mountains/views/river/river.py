from django.views.generic import ListView as DjangoListView, DetailView as DjangoDetailView

from mountains.models import River
from mountains.views.tree import TreeView
from mountains.views.tree.tree import FlatGeoJsonView


class ListView(DjangoListView):
    model = River
    template_name = 'mountains/river/list.html'
    context_object_name = 'rivers'

    def get_queryset(self):
        return River.objects.with_displacement()


class DetailView(DjangoDetailView):
    model = River
    template_name = 'mountains/river/detail.html'


class RiverTreeView(TreeView):
    def get_queryset(self):
        return River.objects.with_source().with_parent()


class GeoJsonView(RiverTreeView, FlatGeoJsonView):
    object_name = 'rivers'
