import math
from abc import ABC

from cairn.views import OrderableListView
from django.db.models import F
from django.views.generic import ListView as DjangoListView, DetailView as DjangoDetailView, ListView

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
        return River.objects.with_displacement().with_tributaries().with_cols()


class RiverForestView(ListView):
    """Server-rendered forest of rivers, ordered by tributary relationship.

    Mirrors the summit prominence forest: the ``tree`` passed to the template is
    an adjacency map ``{parent_pk_or_None -> sorted list of rivers}`` and the
    template recurses by looking up children with ``tree|get_item:river.pk``.
    """
    model = River
    context_object_name = 'rivers'
    template_name = 'mountains/river/tree.html'
    reverse = False

    @staticmethod
    def sort_function(river):
        return river.mouth_altitude if river.mouth_altitude is not None else -math.inf

    @staticmethod
    def parent_fk(river):
        return river.parent_id

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.rivers = []
        self.roots = []
        self.tree = {}

    def get_queryset(self):
        return River.objects.with_source().with_parent().select_related('branches_off__source')

    def process(self):
        self.rivers = self.get_queryset()

        for river in self.rivers:
            self.tree.setdefault(self.parent_fk(river), []).append(river)

        for key, value in self.tree.items():
            self.tree[key] = sorted(value, key=self.sort_function, reverse=self.reverse)

        self.roots = self.tree.get(None, [])

    def get_context_data(self, object_list=None, **kwargs):
        self.process()

        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'tree': self.tree,
        }


class RiverTreeView(TreeView):
    def get_queryset(self):
        return River.objects.with_source().with_parent()


class GeoJsonView(RiverTreeView, FlatGeoJsonView):
    object_name = 'rivers'
