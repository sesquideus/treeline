"""
Tests for the range pages and the range map layer.

Two contracts, neither of which fails loudly when broken:

- the detail page must show every summit at *or below* the range. Showing only the
  memberships pinned exactly at this node renders a perfectly good page that is simply
  missing most of the mountains.
- `properties.type` must be the string `styleFor()` in `static/js/styles.js` knows.
  An unrecognised type falls through to `default: []` and the polygon renders invisibly.
"""

import json
import re

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from mountains.models import Range
from mountains.test_factories import (assign_range, make_range, make_range_system,
                                      make_summit)
from mountains.views.cache import data_version

STYLES_JS = 'static/js/styles.js'


def box(lon, lat, size=0.5):
    """A square MultiPolygon around (lon, lat) — enough of an `area` to serialize."""
    return MultiPolygon(Polygon.from_bbox((lon, lat, lon + size, lat + size)), srid=4326)


class RangePageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.system = make_range_system('sk-geomorf', 'Geomorfologické členenie Slovenska')
        cls.tatry = make_range(cls.system, 'Tatry', level_name='celok', area=box(19.8, 49.1))
        cls.vysoke = make_range(cls.system, 'Vysoké Tatry', cls.tatry, level_name='podcelok')
        cls.zapadne = make_range(cls.system, 'Západné Tatry', cls.tatry, level_name='podcelok')

        cls.gerlach = make_summit('Gerlach', 2655.0, 49.16, 20.13)
        cls.bystra = make_summit('Bystrá', 2248.0, 49.18, 19.77)
        cls.distant = make_summit('Distant', 1000.0, 0.0, 0.0)
        assign_range(cls.gerlach, cls.vysoke)
        assign_range(cls.bystra, cls.zapadne)

    def setUp(self):
        # LocMemCache outlives a test while the database is rolled back after it.
        cache.clear()


class RangeListTests(RangePageTestCase):
    def test_the_list_renders_every_range(self):
        response = self.client.get(reverse('range-list'))
        self.assertEqual(response.status_code, 200)
        for name in ('Tatry', 'Vysoké Tatry', 'Západné Tatry'):
            self.assertContains(response, name)

    def test_the_default_order_walks_the_tree(self):
        """Parent before child, which is what `Meta.ordering` on ('system', 'path') buys."""
        names = [r.name for r in self.client.get(reverse('range-list')).context['ranges']]
        self.assertEqual(names, ['Tatry', 'Vysoké Tatry', 'Západné Tatry'])

    def test_the_count_column_is_the_direct_one(self):
        """Tatry holds no summits itself; both of them sit on its sub-ranges."""
        ranges = {r.name: r for r in self.client.get(reverse('range-list')).context['ranges']}
        self.assertEqual(ranges['Tatry'].direct_summit_count, 0)
        self.assertEqual(ranges['Vysoké Tatry'].direct_summit_count, 1)

    def test_ordering_by_a_column_is_accepted(self):
        response = self.client.get(reverse('range-list'), {'ordering': '-summits'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ranges'][0].direct_summit_count, 1)


class RangeDetailTests(RangePageTestCase):
    def test_the_page_lists_summits_from_the_whole_subtree(self):
        """The contract: 'peaks in the Tatras' means at this range or below, not just here."""
        response = self.client.get(self.tatry.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.context['summits'], [self.gerlach, self.bystra])

    def test_a_leaf_lists_only_its_own(self):
        response = self.client.get(self.vysoke.get_absolute_url())
        self.assertCountEqual(response.context['summits'], [self.gerlach])

    def test_the_breadcrumb_runs_root_first(self):
        response = self.client.get(self.vysoke.get_absolute_url())
        self.assertEqual([r.name for r in response.context['ancestors']], ['Tatry'])

    def test_the_sub_ranges_are_listed(self):
        response = self.client.get(self.tatry.get_absolute_url())
        self.assertCountEqual([r.name for r in response.context['children']],
                              ['Vysoké Tatry', 'Západné Tatry'])

    def test_the_high_point_is_the_highest_below_too(self):
        response = self.client.get(self.tatry.get_absolute_url())
        self.assertEqual(response.context['high_point'], self.gerlach)

    def test_an_empty_range_still_renders(self):
        empty = make_range(self.system, 'Nízke Tatry', self.tatry)
        response = self.client.get(empty.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No summits recorded in this range yet')

    def test_get_absolute_url_resolves(self):
        self.assertEqual(self.tatry.get_absolute_url(),
                         reverse('range-detail', args=[self.tatry.pk]))


class RangeGeoJsonTests(RangePageTestCase):
    def payload(self):
        response = self.client.get(reverse('ranges-geojson'))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content)

    def test_only_ranges_with_a_boundary_are_served(self):
        """Most ranges will never have one; they must be absent, not null features."""
        features = self.payload()['features']
        self.assertEqual([f['properties']['name'] for f in features], ['Tatry'])

    def test_the_feature_carries_the_type_styles_js_knows(self):
        feature = self.payload()['features'][0]
        self.assertEqual(feature['properties']['type'], 'range')
        self.assertEqual(feature['geometry']['type'], 'MultiPolygon')

    def test_styles_js_handles_that_type(self):
        """
        An unknown `type` falls through to `default: []` in `styleFor()` and renders
        invisibly — the failure looks exactly like an empty layer.
        """
        with open(STYLES_JS) as handle:
            self.assertRegex(handle.read(), r"case\s+'range':")

    def test_a_range_without_a_boundary_serializes_to_none(self):
        self.assertIsNone(self.vysoke.to_geojson())

    def test_saving_a_range_drops_the_cached_payload(self):
        """`Range` is in signals.WATCHED; without it the layer would be stale forever."""
        self.payload()
        before = data_version()
        self.zapadne.area = box(19.7, 49.2)
        self.zapadne.save()
        self.assertNotEqual(data_version(), before)
        self.assertCountEqual([f['properties']['name'] for f in self.payload()['features']],
                              ['Tatry', 'Západné Tatry'])


class MapWiringTests(TestCase):
    """
    The map is assembled across three files; these pin the joins that a Python test would
    otherwise never touch and that fail silently in the browser.
    """

    def test_the_map_page_passes_the_ranges_url(self):
        response = self.client.get(reverse('map'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('ranges-geojson'))

    def test_the_controls_offer_a_ranges_toggle(self):
        response = self.client.get(reverse('map'))
        self.assertContains(response, 'toggle-ranges')

    def test_map_js_builds_the_layer_and_keeps_it_out_of_hit_testing(self):
        with open('static/js/map.js') as handle:
            source = handle.read()
        self.assertIn('function buildRangesLayer', source)
        self.assertIn("name !== 'ranges'", source)

    def test_the_menu_offers_the_range_list(self):
        response = self.client.get(reverse('map'))
        self.assertContains(response, reverse('range-list'))
