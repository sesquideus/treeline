import math
from abc import ABC
from typing import Optional

from django.contrib.gis.db.models.functions import Distance
from django.db.models import ExpressionWrapper, F, FloatField, Prefetch, Value
from django.db.models.functions import ATan, Cos, Sin
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView, DetailView, TemplateView, FormView
from django.views.generic.edit import FormMixin

from mountains.forms.summit import CompareForm
from mountains.models import Summit, Col, NamedPoint
from mountains.views.tree.tree import CachedJsonMixin
from mountains.views.viewport import ViewportDetailView


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
        # Metres, not the measure itself: `compute_isolation()` hands back a `Distance` when
        # the queryset annotated one and a geopy distance when it did not, and neither sorts
        # against the 0 that stands in for an unknown isolation.
        isolation = summit.compute_isolation()
        return isolation.m if isolation is not None else 0

    @staticmethod
    def parent_fk(summit):
        return summit.isolation_parent_id

    def get_queryset(self):
        # `with_isolation()`: the node template prints `summit.isolation`, which was never
        # annotated here, so every row silently lost its distance.
        return (Summit.objects.with_isolation()
                .select_related('isolation_parent__point', 'point'))



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


PARENT_LINKS = ('prominence_parent', 'slope_parent', 'horizon_parent', 'horizon_parent_std')


def prime_lineages(summit):
    """
    Fill in one summit's parent chains, so the lineage templates can walk them without a
    query per level.

    The four lineage tags recurse through `*_parent`, and the chains are long: prominence
    reaches fifteen links in the current data, slope nine. `select_related` cannot cover
    that — its joins are paid on every request whether the chain is that long or not, and
    measured on this page fifteen levels cost about three seconds of SQL, four levels still
    more than this. So: one query for every parent link in the table, walked in Python to
    find the summits on this one's chains, then one query for those summits. Assigning a
    related object is what primes Django's FK cache, so the templates never go back to the
    database — at any depth.
    """
    links = {pk: parents for pk, *parents in
             Summit.objects.values_list('pk', *[f'{attr}_id' for attr in PARENT_LINKS])}

    needed, frontier = set(), {summit.pk}
    while frontier:                     # `needed` also stops a cycle, should the data hold one
        following = set()
        for pk in frontier:
            for parent in links.get(pk, ()):
                if parent is not None and parent not in needed:
                    needed.add(parent)
                    following.add(parent)
        frontier = following

    # `key_col__point`: the prominence lineage names the col of every level, and
    # compute_prominence() reads its altitude.
    chain = {s.pk: s for s in Summit.objects.filter(pk__in=needed)
             .select_related('point', 'key_col__point')}
    for obj in (summit, *chain.values()):
        for attr, parent_pk in zip(PARENT_LINKS, links.get(obj.pk, ())):
            if (parent := chain.get(parent_pk)) is not None:
                setattr(obj, attr, parent)
    return summit


class MountainDetailView(DetailView):
    model = Summit
    context_object_name = 'mountain'
    template_name = 'mountains/summit/detail.html'

    def get_queryset(self):
        return (Summit.objects
                .with_prominence().with_isolation().with_slope_parent()
                # The names table reads point.names and each name's language.
                .prefetch_related('point__names__language')
                .prefetch_related(
                    # `with_isolation()`: the block prints each child's `isolation`, which
                    # nothing annotated, so its distance column was always empty.
                    Prefetch('isolation_children',
                             queryset=Summit.objects.with_isolation()),
                    Prefetch('prominence_children',
                             queryset=Summit.objects.with_prominence()
                             .select_related('key_col__point').order_by('-prominence')),
                ))

    def ranked_against_this(self):
        """
        Every other summit, with the four numbers the "slope to" and "horizon to" tables
        quote — measured from this one. The same expressions as `with_slope_parent()` and
        `with_horizon_parent()`, with a fixed point in place of the parent, so that the
        ranking happens in the database: in Python it is four geodesics per summit, some
        eight thousand of them, and the slowest thing on the page by an order of magnitude.
        """
        point = self.object.point.location
        altitude = self.object.point.altitude
        distance = Distance('point__location', point)

        def angle(refraction):
            r = 6371000.0 / (1 - refraction)
            beta = distance / Value(r)
            return ATan(
                ((Value(r) + F('point__altitude')) * Cos(beta) - Value(r + altitude))
                / ((Value(r) + F('point__altitude')) * Sin(beta))
            )

        return (Summit.objects
                .exclude(pk=self.object.pk).exclude(point__location__isnull=True)
                .select_related('point')
                .annotate(
                    distance=distance,
                    dh=ExpressionWrapper(F('point__altitude') - Value(altitude),
                                         output_field=FloatField()),
                    hhp_angle=angle(0.0),
                    hhp_angle_std=angle(0.14),
                )
                .annotate(slope=F('dh') / F('distance')))

    def get_context_data(self, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        prime_lineages(self.object)
        ranked = self.ranked_against_this()
        # Descending, nulls last: Postgres sorts NULLs first on a descending order, and a
        # summit whose metric could not be computed would head the table.
        return context | {
            'by_slope': ranked.order_by(F('slope').desc(nulls_last=True))[:20],
            'by_horizon': ranked.order_by(F('hhp_angle').desc(nulls_last=True))[:20],
        }


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


class GeoJsonView(CachedJsonMixin, View):
    """
    Every summit, as the skeleton the global map draws from: position, name, prominence and
    the ids it joins lineage on. The popup half comes from `DetailJsonView` per viewport.
    """
    model = Summit

    def build_payload(self):
        return {
            'type': 'FeatureCollection',
            'features': Summit.objects.skeleton_features(),
        }


class DetailJsonView(ViewportDetailView):
    """
    Popup detail for the summits in a viewport — the most prominent first, so that is what
    survives the limit. `nulls_last`: Postgres sorts NULLs first on a descending order, and
    a summit whose prominence is unknown for want of a key col would otherwise crowd out the
    ultras a reader is actually looking at.
    """
    ordering = (F('prominence').desc(nulls_last=True),)

    def get_queryset(self):
        # The annotations are what `to_detail_dict()` reads instead of recomputing every
        # distance, slope and angle in Python, per summit — and with_slope_parent() /
        # with_horizon_parent() also join the two parents whose sections it serializes.
        return (Summit.objects
                .with_prominence().with_isolation()
                .with_distance_to_key_col().with_slope_to_key_col()
                .with_slope_parent().with_horizon_parent().with_countries())
