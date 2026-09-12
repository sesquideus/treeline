"""
A check-style sweep over every custom QuerySet method in the project.

`manage.py check` validates model *declarations*; it never runs a query, so a `with_*`
method that names a relation which does not exist, or annotates over a join that was
renamed, stays silent until a page happens to call it. Most of these methods are called
from exactly one view, and a few from none at all.

So this module runs all of them. `CASES` is the registry of what each one promises — its
annotations, and the related objects it is supposed to have loaded up front — and the
coverage tests below fail when a method is added to a QuerySet without an entry here, or
when a QuerySet class is never attached to a model at all. The point is that adding a
`with_*` method and forgetting to exercise it should not be possible quietly.
"""

import sys
from dataclasses import dataclass

from django.apps import apps
from django.contrib.gis.geos import Point
from django.core.exceptions import ObjectDoesNotExist
from django.db import models as dm
from django.test import TestCase

from core.models import Country, Language
from mountains.models import Col, Confluence, River, Summit
from mountains.models.point import NamedPoint, PointName
from mountains.test_factories import make_col, make_point, make_summit

#: apps whose querysets this sweep owns
APP_LABELS = ('mountains', 'core', 'users')


@dataclass(frozen=True)
class Case:
    """
    What one queryset method promises.

    annotations
        names that must be present on every returned row.
    cached
        dotted attribute chains that must already be loaded once the queryset has been
        evaluated — a `select_related` or `prefetch_related` that silently stopped
        matching still returns correct rows, it just costs one query per row.
    terminal
        the method returns a plain list of GeoJSON features rather than a queryset, so it
        neither chains nor carries annotations.
    builds_on
        methods this one already applies internally, so stacking it on top of them would
        annotate or prefetch the same names twice.
    conflicts_with
        methods it cannot be stacked with at all, because both claim the same prefetch
        lookup with a queryset of their own.
    """
    annotations: tuple[str, ...] = ()
    cached: tuple[str, ...] = ()
    terminal: bool = False
    builds_on: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()


CASES: dict[type[dm.Model], dict[str, Case]] = {
    Summit: {
        'with_point': Case(cached=('point',)),
        'with_prominence': Case(
            annotations=('prominence', 'dominance', 'distance_to_parent'),
            cached=('point', 'key_col.point', 'prominence_parent.point'),
        ),
        'with_distance_to_key_col': Case(annotations=('distance_to_key_col',)),
        'with_slope_to_key_col': Case(
            annotations=('key_col_dh', 'key_col_dd', 'slope_to_key_col'),
        ),
        'with_isolation': Case(
            annotations=('isolation',),
            cached=('point', 'isolation_parent.point'),
        ),
        'with_countries': Case(cached=('point.countries',)),
        'with_slope_parent': Case(
            annotations=('dh', 'dd', 'slope'),
            cached=('slope_parent.point',),
        ),
        'with_horizon_parent': Case(
            annotations=('distance_to_horizon', 'beta', 'angle'),
            cached=('horizon_parent.point',),
        ),
        'with_confluence': Case(cached=('key_col.confluence.point',)),
        'with_ultras': Case(annotations=('prominence', 'ultra'),
                            builds_on=('with_prominence',)),
        'with_full_name': Case(annotations=('full_name',)),
        'with_complete': Case(
            annotations=('has_point', 'has_key_col', 'has_prominence_parent',
                         'has_isolation', 'complete'),
            builds_on=('with_prominence',),
        ),
        'only_complete': Case(annotations=('complete',),
                              builds_on=('with_prominence', 'with_complete')),
        'by_angle': Case(
            annotations=('angle',),
            cached=('point', 'horizon_parent.point'),
            builds_on=('with_point', 'with_horizon_parent'),
        ),
        'skeleton_features': Case(terminal=True),
    },
    Col: {
        'with_siblings': Case(cached=('key_for.prominence_children',),
                              conflicts_with=('with_minor',)),
        'with_point': Case(cached=('point',)),
        'with_minor': Case(annotations=('depth',), cached=('key_for',)),
        'with_rivers': Case(cached=('confluence_river.source',
                                    'confluence_river.parent.source')),
        'with_countries': Case(cached=('point.countries',)),
        'with_full_name': Case(annotations=('full_name',)),
        'skeleton_features': Case(terminal=True),
    },
    River: {
        'with_source': Case(cached=('source', 'source.names')),
        'with_parent': Case(cached=('parent', 'parent.source.names')),
        'with_full_name': Case(annotations=('full_name',)),
        'with_displacement': Case(annotations=('displacement',)),
        'with_tributaries': Case(cached=('tributaries',)),
        'with_branches': Case(cached=('branches',)),
        'with_cols': Case(cached=('cols',)),
        'with_direct_length': Case(annotations=('direct_length',)),
        'with_db_status': Case(annotations=('complete',)),
    },
    NamedPoint: {
        'with_names': Case(cached=('names',)),
    },
}


def queryset_methods(model):
    """The public methods a model's queryset adds on top of a plain `QuerySet`."""
    base = set(dir(dm.QuerySet))
    return {
        name for name in dir(type(model.objects.all()))
        if not name.startswith('_') and name not in base
    }


def project_models():
    return [model for label in APP_LABELS for model in apps.get_app_config(label).get_models()]


def declared_querysets():
    """Every `QuerySet` subclass defined anywhere under an app's `models` package."""
    found = set()
    prefixes = tuple(f'{label}.models' for label in APP_LABELS)
    for name, module in list(sys.modules.items()):
        if not name.startswith(prefixes) or module is None:
            continue
        for attribute in vars(module).values():
            if (isinstance(attribute, type) and issubclass(attribute, dm.QuerySet)
                    and attribute is not dm.QuerySet
                    and attribute.__module__.startswith(prefixes)):
                found.add(attribute)
    return found


class QuerySetCoverageTests(TestCase):
    """Structural checks — these need no fixture, only the class definitions."""

    def test_every_queryset_method_has_a_case(self):
        """A new `with_*` method has to be registered in `CASES`, or it goes unexercised."""
        for model in project_models():
            expected = queryset_methods(model)
            with self.subTest(model=model.__name__):
                self.assertEqual(set(CASES.get(model, {})), expected)

    def test_no_case_names_a_method_that_is_gone(self):
        for model, cases in CASES.items():
            for name in cases:
                with self.subTest(model=model.__name__, method=name):
                    self.assertTrue(callable(getattr(model.objects.all(), name, None)))

    def test_every_queryset_class_is_attached_to_a_model(self):
        """
        A `*QuerySet` that no model uses as its manager is unreachable: nothing it declares
        is ever run, so nothing there can be trusted to still work.
        """
        attached = {type(model.objects.all()) for model in project_models()}
        for candidate in declared_querysets():
            with self.subTest(queryset=candidate.__name__):
                self.assertIn(candidate, attached)


class QuerySetBehaviourTests(TestCase):
    """
    One fully wired example of each model, so that every relation the registry lists as
    cached actually has something on the other end — a prefetch over an empty relation
    looks identical to a working one.
    """

    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(code='sk', name='Slovensko',
                                         full_name='Slovenská republika', english_name='Slovakia')
        language = Language.objects.create(iso639_1='sk', iso639_3='slk', country_code='sk',
                                           name='slovenčina', english_name='Slovak', priority=1)

        big_river = River.objects.create(
            source=make_point('Big source', 900.0, 49.3, 20.9),
            mouth=Point(21.0, 48.7, srid=4326), mouth_altitude=300.0)
        river = River.objects.create(
            source=make_point('Brook source', 1500.0, 49.05, 20.4),
            mouth=Point(20.6, 48.9, srid=4326), mouth_altitude=500.0,
            parent=big_river, branches_off=big_river)
        PointName.objects.create(point=river.source, name='Potok', language=language)
        PointName.objects.create(point=big_river.source, name='Rieka', language=language)

        confluence = Confluence.objects.create(point=make_point('Confluence', 500.0, 48.9, 20.6))
        col = make_col('Alpha col', 2500.0, 49.0, 20.5,
                       confluence=confluence, confluence_river=river)
        child_col = make_col('Beta col', 1800.0, 49.05, 20.05)

        alpha = make_summit('Alpha', 3000.0, 49.0, 20.0, key_col=col,
                            nearest_higher_point=Point(21.0, 49.0, srid=4326))
        beta = make_summit('Beta', 2000.0, 49.1, 20.1, key_col=child_col,
                           prominence_parent=alpha, isolation_parent=alpha,
                           slope_parent=alpha, horizon_parent=alpha,
                           nearest_higher_point=Point(20.05, 49.05, srid=4326))
        river.source_summit = alpha
        river.parent_summit = alpha
        river.save()

        for summit in (alpha, beta):
            summit.point.countries.add(country)

    def build(self, model, name):
        return getattr(model.objects.all(), name)()

    def test_each_method_returns_rows(self):
        """The plainest check there is: the SQL is valid and the relations all resolve."""
        for model, cases in CASES.items():
            for name, case in cases.items():
                with self.subTest(model=model.__name__, method=name):
                    result = self.build(model, name)
                    rows = list(result)
                    self.assertTrue(rows, 'the fixture should have matched something')
                    if case.terminal:
                        self.assertTrue(all(row['type'] == 'Feature' for row in rows))

    def test_each_method_delivers_its_annotations(self):
        for model, cases in CASES.items():
            for name, case in cases.items():
                for annotation in case.annotations:
                    with self.subTest(model=model.__name__, method=name, annotation=annotation):
                        row = self.build(model, name).first()
                        self.assertTrue(hasattr(row, annotation))

    def test_related_objects_are_loaded_up_front(self):
        """
        Each cached chain must cost nothing to walk once the queryset has been evaluated;
        a query here means the `select_related`/`prefetch_related` no longer matches what
        the method's callers touch.
        """
        for model, cases in CASES.items():
            for name, case in cases.items():
                for chain in case.cached:
                    with self.subTest(model=model.__name__, method=name, chain=chain):
                        rows = list(self.build(model, name))
                        with self.assertNumQueries(0):
                            for row in rows:
                                walk(row, chain)

    def test_chainable_methods_compose(self):
        """
        Views stack these freely, so no two may collide — a repeated annotation name
        raises, and a clashing one silently changes what an ordering or filter means.
        Composites and the declared conflicts below are left out; everything else has to
        survive being applied all at once.
        """
        for model, cases in CASES.items():
            names = [name for name, case in cases.items()
                     if not (case.terminal or case.builds_on or case.conflicts_with)]
            with self.subTest(model=model.__name__):
                queryset = model.objects.all()
                for name in names:
                    queryset = getattr(queryset, name)()
                self.assertTrue(list(queryset))

    def test_a_composite_can_be_applied_on_its_own(self):
        """
        `with_ultras` and friends apply `with_prominence` themselves, so a caller reaches
        for one *instead of* its parts. Each still has to stand alone.
        """
        for model, cases in CASES.items():
            for name, case in cases.items():
                if not case.builds_on:
                    continue
                with self.subTest(model=model.__name__, method=name):
                    self.assertTrue(list(self.build(model, name)))

    def test_declared_conflicts_still_conflict(self):
        """
        Both sides claim one prefetch lookup with a queryset of their own, which Django
        refuses. Recorded here so a view author finds out from the registry rather than
        from a 500 — if this starts passing, the pair became stackable and the
        `conflicts_with` note should go.
        """
        for model, cases in CASES.items():
            for name, case in cases.items():
                for other in case.conflicts_with:
                    with self.subTest(model=model.__name__, pair=f'{name}+{other}'):
                        queryset = getattr(getattr(model.objects.all(), name)(), other)()
                        with self.assertRaises(ValueError):
                            list(queryset)


def walk(obj, chain):
    """Follow a dotted attribute chain, evaluating any related manager it ends on."""
    for attribute in chain.split('.'):
        if obj is None:
            return None
        try:
            obj = getattr(obj, attribute)
        except ObjectDoesNotExist:
            # A missing reverse one-to-one is cached as a miss; no query is issued.
            return None
    return list(obj.all()) if hasattr(obj, 'all') else obj
