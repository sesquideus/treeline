"""
Tests for the mountain list's range filters.

`RangeWidget` names its two halves `<field>_min` / `<field>_max` rather than taking
MultiWidget's positional `_0` / `_1`, because those names are what a shared or bookmarked
filter URL carries. The querystring is therefore part of the contract and is pinned here,
alongside the range semantics — one-sided bounds, empty ranges, and inverted ones.
"""

from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse

from mountains.forms.filter import SummitFilterForm
from mountains.test_factories import make_col, make_summit

RANGE_FIELDS = ('altitude', 'prominence', 'isolation')


class RangeQueryStringTests(TestCase):
    def test_each_range_renders_a_min_and_a_max_input(self):
        html = str(SummitFilterForm())
        for field in RANGE_FIELDS:
            for bound in ('min', 'max'):
                with self.subTest(field=field, bound=bound):
                    self.assertIn(f'name="{field}_{bound}"', html)

    def test_the_positional_names_are_gone(self):
        html = str(SummitFilterForm())
        for field in RANGE_FIELDS:
            for index in ('0', '1'):
                with self.subTest(field=field, index=index):
                    self.assertNotIn(f'name="{field}_{index}"', html)

    def test_both_bounds_clean_to_a_low_high_tuple(self):
        form = SummitFilterForm({'altitude_min': '2000', 'altitude_max': '3000'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['altitude'], (2000.0, 3000.0))

    def test_a_one_sided_bound_leaves_the_other_end_open(self):
        form = SummitFilterForm({'prominence_min': '100', 'isolation_max': '50'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['prominence'], (100.0, None))
        self.assertEqual(form.cleaned_data['isolation'], (None, 50.0))

    def test_an_empty_range_cleans_to_none(self):
        form = SummitFilterForm({})
        self.assertTrue(form.is_valid(), form.errors)
        for field in RANGE_FIELDS:
            with self.subTest(field=field):
                self.assertIsNone(form.cleaned_data[field])

    def test_an_inverted_range_is_rejected(self):
        form = SummitFilterForm({'altitude_min': '3000', 'altitude_max': '2000'})
        self.assertFalse(form.is_valid())
        self.assertIn('altitude', form.errors)


class RangeFilterTests(TestCase):
    """
    Three summits a degree apart, each with a key col 500 m below it and a nearest higher
    point one degree east, so all three ranges have something to bite on:

        Alpha 3000 m   Beta 2000 m   Gamma 1000 m
    """

    @classmethod
    def setUpTestData(cls):
        for name, altitude, lon in (('Alpha', 3000.0, 20.0),
                                    ('Beta', 2000.0, 21.0),
                                    ('Gamma', 1000.0, 22.0)):
            col = make_col(f'{name} col', altitude - 500, 49.0, lon + 0.5)
            make_summit(name, altitude, 49.0, lon, key_col=col,
                        nearest_higher_point=Point(lon + 1, 49.0, srid=4326))

    def filtered(self, **params):
        response = self.client.get(reverse('mountain-list'), params)
        self.assertEqual(response.status_code, 200)
        return [m.point.name for m in response.context['mountains']]

    def test_altitude_range(self):
        self.assertEqual(self.filtered(altitude_min=1500, altitude_max=2500), ['Beta'])
        self.assertEqual(self.filtered(altitude_min=1500), ['Alpha', 'Beta'])
        self.assertEqual(self.filtered(altitude_max=1500), ['Gamma'])

    def test_prominence_range(self):
        """Every summit here has 500 m of prominence, so the range either takes all or none."""
        self.assertEqual(self.filtered(prominence_min=400, prominence_max=600),
                         ['Alpha', 'Beta', 'Gamma'])
        self.assertEqual(self.filtered(prominence_min=600), [])

    def test_isolation_range_is_in_kilometres(self):
        """One degree of longitude at 49°N is ~73 km, well outside a 10 km bound."""
        self.assertEqual(self.filtered(isolation_max=10), [])
        self.assertEqual(self.filtered(isolation_min=10), ['Alpha', 'Beta', 'Gamma'])

    def test_no_range_parameters_filters_nothing(self):
        self.assertEqual(self.filtered(), ['Alpha', 'Beta', 'Gamma'])

    def test_an_inverted_range_filters_nothing_and_reports_the_error(self):
        response = self.client.get(reverse('mountain-list'),
                                   {'altitude_min': 3000, 'altitude_max': 2000})
        self.assertEqual(response.status_code, 200)
        self.assertIn('altitude', response.context['filter_form'].errors)
        self.assertEqual([m.point.name for m in response.context['mountains']],
                         ['Alpha', 'Beta', 'Gamma'])
