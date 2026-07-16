import math
from abc import ABC
from typing import Optional

from cairn.views import OrderableListView
from django.db.models import F, Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, DetailView, TemplateView, FormView
from django.views.generic.edit import FormMixin

from mountains.forms.filter import FilterForm
from mountains.forms.summit import CompareForm
from mountains.models import Summit, Col
from mountains.views.tree.tree import FlatGeoJsonView


class SummitTreeView(ListView, ABC):
    model = Summit
    context_object_name = 'mountains'
    reverse = True

    @staticmethod
    def sort_function(summit):
        return summit

    @staticmethod
    def parent_fk(summit):
        return summit

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.summits = []
        self.roots = []
        self.tree = {}

    def preprocess(self):
        pass

    def process(self):
        self.summits = self.get_queryset()
        self.preprocess()

        for mountain in self.summits:
            self.tree.setdefault(self.parent_fk(mountain), []).append(mountain)

        for key, value in self.tree.items():
            self.tree[key] = sorted(value, key=self.sort_function, reverse=self.reverse)

        self.roots = self.tree.get(None, [])

    def get_context_data(self, object_list=None, **kwargs):
        # Inject the preprocessing step here
        self.process()

        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'tree': self.tree,
        }


class ProminenceForestView(SummitTreeView):
    template_name = 'mountains/summit/prominence/tree.html'

    @staticmethod
    def sort_function(summit):
        return summit.prominence or 0

    @staticmethod
    def parent_fk(summit):
        return summit.prominence_parent_id

    def get_queryset(self):
        return Summit.objects.with_prominence().select_related('point', 'key_col', 'key_col__point', 'prominence_parent__point').prefetch_related('key_col__key_for__point')


class IsolationForestView(SummitTreeView):
    template_name = 'mountains/summit/isolation/tree.html'

    @staticmethod
    def sort_function(summit):
        return summit.compute_isolation() or 0

    @staticmethod
    def parent_fk(summit):
        return summit.isolation_parent_id

    def get_queryset(self):
        return Summit.objects.select_related('isolation_parent__point', 'point')



class SlopeTreeView(SummitTreeView):
    template_name = 'mountains/summit/slope/tree.html'
    reverse = False

    @staticmethod
    def sort_function(summit):
        return summit.point.slope_to(summit.slope_parent.point) if summit.slope_parent else -math.inf

    @staticmethod
    def parent_fk(summit):
        return summit.slope_parent_id

    def preprocess(self):
        for mountain in self.summits:
            mountain.slope = mountain.point.slope_to(mountain.slope_parent.point) if mountain.slope_parent else None

    def get_queryset(self):
        return Summit.objects.select_related('slope_parent__point', 'point')

    def get_context_data(self, object_list=None, **kwargs):
        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'mountain_map': self.tree,
        }


class HorizonTreeView(SummitTreeView):
    template_name = 'mountains/summit/horizon/tree.html'

    @staticmethod
    def sort_function(summit):
        return summit.point.angle_to(summit.horizon_parent.point) if summit.horizon_parent else -90

    @staticmethod
    def parent_fk(summit):
        return summit.horizon_parent_id

    def preprocess(self):
        for mountain in self.summits:
            mountain.hhp_angle = mountain.point.angle_to(mountain.horizon_parent.point) if mountain.horizon_parent else None
            mountain.hhp_angle_std = mountain.point.angle_to(mountain.horizon_parent_std.point) if mountain.horizon_parent_std else None

    def get_queryset(self):
        return Summit.objects.select_related('horizon_parent__point', 'point')


class MountainDetailView(DetailView):
    model = Summit
    context_object_name = 'mountain'
    template_name = 'mountains/summit/detail.html'

    def get_queryset(self):
        return Summit.objects.with_prominence().with_isolation().with_slope_parent().prefetch_related(
            Prefetch('prominence_children',
                     queryset=Summit.objects.with_prominence().select_related('key_col__point').order_by('-prominence'))
        )

    def get_context_data(self, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        summits = Summit.objects.all().exclude(id=self.object.id).select_related('point')

        for s in summits:
            s.slope = self.object.point.slope_to(s.point)
            s.hhp_angle = self.object.point.angle_to(s.point)
            s.hhp_angle_std = self.object.point.angle_to(s.point, refraction=0.14)
            s.dh = s.point.altitude - self.object.point.altitude
            s.distance = s.point.distance_to(self.object.point)

        return context | {
            'by_slope': sorted(summits, key=lambda x: x.slope, reverse=True)[:20],
            'by_horizon': sorted(summits, key=lambda x: x.hhp_angle, reverse=True)[:20],
        }

class MountainListView(OrderableListView):
    model = Summit
    context_object_name = 'mountains'
    template_name = 'mountains/summit/list.html'

    ORDERING = {
        'name': 'point__name',
        'altitude': 'point__altitude',
        'parent-name': 'prominence_parent__point__name',
        'parent-altitude': 'prominence_parent__point__altitude',
        'prominence': 'prominence',
        'dominance': 'dominance',
        'key-col': 'key_col__point__name',
        'key-col-alt': 'key_col__point__altitude',
        'nhn': 'isolation_parent__point__name',
        'isolation': 'isolation',
        'slope': 'slope',
        'horizon': 'angle',
    }

    def parse_get_arguments(self):
        super().parse_get_arguments()
        self.countries = self.request.GET.get('countries', '').split(',')

    def get_queryset(self):
        qs = Summit.objects.with_point().with_prominence().with_isolation().with_slope_parent().with_horizon_parent().with_countries().with_full_name().distinct()

        if self.ordering:
            if self.ordering[0] == '-':
                ordering = self.ordering[1:]
                qs = qs.order_by(F(ordering).desc(nulls_last=True))
            else:
                ordering = self.ordering
                qs = qs.order_by(F(ordering).asc(nulls_last=True))

        if self.countries and self.countries != ['']:
            qs = qs.filter(point__countries__code__in=self.countries)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context |= {
            'filter_form': FilterForm(),
        }

        return context

class SlopeToView(DetailView):
    model = Summit
    context_object_name = 'mountain'

    def get_context_data(self, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        return context | {
            'summits': Summit.objects.all()
        }


def separation(s1, s2) -> Optional[tuple[Optional[Summit], Optional[Col]]]:
    # collect path from s1 to root: {pk: summit}
    path1 = {}
    current = s1
    while current is not None:
        path1[current.pk] = current
        current = current.prominence_parent

    # walk s2 upward until we hit a summit in path1 — that's the LCA
    path2 = []
    current = s2
    lca = None
    while current is not None:
        if current.pk in path1:
            lca = current
            break
        path2.append(current)
        current = current.prominence_parent

    if lca is None:
        return None

    # collect path1 up to (not including) the LCA
    path1_to_lca = []
    current = s1
    while current.pk != lca.pk:
        path1_to_lca.append(current)
        current = current.prominence_parent

    # the separating col is the lowest key col on the union of both paths
    candidates = path1_to_lca + path2

    if not (cols := [s.key_col for s in candidates if s.key_col and s.key_col.point]):
        return None

    return lca, min(cols, key=lambda c: c.point.altitude)


class SummitCompareView(FormMixin, TemplateView):
    """
    A view of a pair of mountains. Shold display distance, slope, horizon angle, mutual key col and so on.
    """
    template_name = 'mountains/summit/comparison.html'
    form_class = CompareForm

    def get_form_initial(self):
        initial = {
            'summit1': self.request.GET.get('summit1'),
            'summit2': self.request.GET.get('summit2'),
        }
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['initial'] = self.get_form_initial()
        return kwargs

    def get_summits(self):
        pk1 = self.request.GET.get('summit1')
        pk2 = self.request.GET.get('summit2')
        if pk1 and pk2:
            return (
                get_object_or_404(Summit.objects.select_related('point'), pk=pk1),
                get_object_or_404(Summit.objects.select_related('point'), pk=pk2),
            )
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.get_form()
        summits = self.get_summits()

        if summits:
            s1, s2 = summits
            sep = separation(s1, s2)
            context['s1'], context['s2'] = summits
            context['distance'] = s1.point.distance_to(s2.point)
            context['slope'] = s1.point.slope_to(s2.point)
            context['angle'] = s1.point.angle_to(s2.point)
            if sep:
                context['lca'], context['sep_col'] = sep
        return context


class GeoJsonView(FlatGeoJsonView):
    model = Summit

    def get_queryset(self):
        return Summit.objects.with_prominence().with_isolation().with_slope_parent().with_horizon_parent().with_countries()
