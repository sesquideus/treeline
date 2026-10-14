import math
from abc import ABC
from dataclasses import dataclass, replace
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


@dataclass(frozen=True)
class ChainStep:
    """
    One rung of the way between two peaks: a summit, or the col crossed to reach the next.

    `obj` is `None` only for a key col that is not recorded. The row stays, empty, rather
    than being dropped — the table claims to be the whole chain, and a silently missing
    rung would make a link look like no link at all.
    """

    obj: Optional[Summit | Col]
    kind: str
    direction: str = ''
    lowest: bool = False

    #: What each kind is called in the leftmost column, in the reader's words rather than
    #: the model's: an intermediate summit is reached as somebody's prominence parent.
    ROLES = {'peak': 'peak', 'summit': 'parent', 'ancestor': 'common ancestor',
             'col': 'key col'}

    @property
    def role(self):
        return self.ROLES[self.kind]

    @property
    def css(self):
        """The class `object_link` paints the link with — also the shape of the thing."""
        return 'col' if self.kind == 'col' else 'mountain'


def prominence_chain(s1, s2) -> list[ChainStep]:
    """
    The whole way from one peak to the other, as alternating summits and cols.

    Peak, key col, parent, key col, … up to the summit the two share, and back down the
    same way to the other peak — the chain the map draws, in the order a reader walks it.
    Because a prominence parent is always the higher ground, the first half only climbs and
    the second only descends, which is what `direction` records.

    The lowest col is flagged here rather than searched for twice: `separation()` and the
    table both read this one list, so the prose and the rows cannot come to name different
    saddles.

    Empty when the two sit in different trees — a forest with a root per landmass, so this
    is ordinary rather than an error: there is simply no way between them to show.
    """
    ancestor, mine, theirs = s1.common_prominence_ancestry(s2)
    if ancestor is None:
        return []

    steps = []
    for index, summit in enumerate(mine):
        steps.append(ChainStep(summit, 'peak' if index == 0 else 'summit', 'up'))
        steps.append(ChainStep(summit.key_col, 'col', 'up'))
    steps.append(ChainStep(ancestor, 'ancestor'))
    # Downwards the rungs come in the other order, but each col is still the key col of the
    # summit *below* it: the link it sits on is the one it has always belonged to.
    for index, summit in reversed(list(enumerate(theirs))):
        steps.append(ChainStep(summit.key_col, 'col', 'down'))
        steps.append(ChainStep(summit, 'peak' if index == 0 else 'summit', 'down'))

    cols = [(position, step) for position, step in enumerate(steps)
            if step.kind == 'col' and step.obj and step.obj.point]
    if cols:
        position, step = min(cols, key=lambda pair: pair[1].obj.point.altitude)
        steps[position] = replace(step, lowest=True)
    return steps


def separation(s1, s2) -> Optional[tuple[Summit, Optional[Col]]]:
    """
    The summit standing above both peaks, and the lowest col on the way between them.

    That col is the one that actually separates them: walk from either peak up to the
    ancestor they share, and the lowest saddle crossed is as deep as the ground sinks
    between the two. The ancestor's own key col is not a candidate — it belongs to the
    ground above the fork rather than to anything between the peaks.

    The col can be missing while the ancestor is not: a chain whose summits have no key col
    recorded yet still joins the two. They are reported separately rather than as one
    all-or-nothing answer, because a page that knows the common ancestor should say so even
    when it cannot name a saddle. `None` means there is no ancestor at all — the two sit in
    different trees.
    """
    return chain_summary(prominence_chain(s1, s2))


def chain_summary(chain) -> Optional[tuple[Summit, Optional[Col]]]:
    """
    The two things a chain is asked for by name: where it forks, and its deepest col.

    Split out from `separation()` so the view can read them off the chain it already has
    instead of walking the hierarchy a second time.
    """
    ancestor = next((step.obj for step in chain if step.kind == 'ancestor'), None)
    if ancestor is None:
        return None
    return ancestor, next((step.obj for step in chain if step.lowest), None)


class SummitCompareView(FormMixin, TemplateView):
    """
    A pair of mountains side by side: how far apart they are, the lowest col on the way
    between them, and the summit they both descend from.

    The pair comes either from the form, as `?summit1=&summit2=`, or from the path, as
    `/summit/compare/<pk1>/<pk2>/` — the two routes this view is wired to. The query string
    is what the form submits; the path is what makes a comparison linkable.
    """
    template_name = 'mountains/summit/comparison.html'
    form_class = CompareForm

    FIELDS = ('summit1', 'summit2')

    def get_form(self, form_class=None):
        """
        One form per request, not one per call.

        `get_summits()` and `get_context_data()` both want it, and the second has to be the
        one that was validated — otherwise the page renders a fresh, blank form and the
        reason a comparison did not happen is thrown away.
        """
        if not hasattr(self, '_form'):
            self._form = super().get_form(form_class)
        return self._form

    def get_form_kwargs(self):
        """
        Bound to the query string whenever the boxes were submitted, so that what they hold
        is read by the field that understands it — the search boxes carry a name, and the
        view used to take the query string for primary keys and hand `Kriváň` straight to
        `get_object_or_404`, which is a 500. Bound on either box so a half-filled form says
        which half is missing.
        """
        kwargs = super().get_form_kwargs()
        kwargs['initial'] = {'summit1': self.kwargs.get('pk1'),
                             'summit2': self.kwargs.get('pk2')}
        if any(field in self.request.GET for field in self.FIELDS):
            kwargs['data'] = self.request.GET
        return kwargs

    def get_summits(self):
        """
        The pair being compared: named in the path, or typed into the boxes.

        `key_col__point` because every answer on this page is measured against the cols: the
        prominence of each peak, and the first link of each chain up to the summit they
        share. The form's own queryset carries it too.
        """
        if 'pk1' in self.kwargs and 'pk2' in self.kwargs:
            summits = Summit.objects.select_related('point', 'key_col__point')
            return (get_object_or_404(summits, pk=self.kwargs['pk1']),
                    get_object_or_404(summits, pk=self.kwargs['pk2']))
        form = self.get_form()
        if form.is_bound and form.is_valid():
            return tuple(form.cleaned_data[field] for field in self.FIELDS)
        return None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.get_form()
        summits = self.get_summits()

        if summits:
            s1, s2 = summits
            chain = prominence_chain(s1, s2)
            context['s1'], context['s2'] = summits
            context['distance'] = s1.point.distance_to(s2.point)
            context['slope'] = s1.point.slope_to(s2.point)
            context['angle'] = s1.point.angle_to(s2.point)
            context['chain'] = chain
            # Read off the chain rather than walked for again, and separately rather than as
            # a pair that stands or falls together: the ancestor can be known when no col on
            # the way between them is.
            summary = chain_summary(chain)
            context['lca'], context['sep_col'] = summary if summary else (None, None)
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
