"""
`map/features.json`: what the global map loads for one view, and the stored zooms and edges
it is answered from (`mountains.map_zooms`).

The view in these tests is the box 19.9–20.1 °E, 48.9–49.1 °N. Every summit shows from zoom
5 unless a test says otherwise, and the request asks for zoom 10, so what decides is where
things stand and how they are linked.
"""
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from mountains.map_zooms import built_for, refresh_and_record_map_zooms
from mountains.models import Col, River, SummitMapZoom
from mountains.test_factories import fixture_source, make_col, make_summit
from mountains.views.cache import bump_data_version, data_version

VIEW = '19.9,48.9,20.1,49.1'


class MapFeaturesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # G ← P ← C1 ← D, by prominence; everything at lat 49.0 unless it matters.
        cls.g = make_summit('G', 2000, 49.0, 25.0, island_high_point=True)
        cls.p = make_summit('P', 1500, 49.0, 20.5, key_col=make_col(None, 800, 49.0, 22.0),
                            prominence_parent=cls.g)
        cls.k1 = make_col(None, 900, 49.0, 20.2)
        cls.c1 = make_summit('C1', 1200, 49.0, 20.0, key_col=cls.k1, prominence_parent=cls.p)
        cls.d = make_summit('D', 1000, 49.0, 20.3, key_col=make_col(None, 950, 49.0, 20.25),
                            prominence_parent=cls.c1)
        # Both ends outside, its routed edge straight across the view.
        cls.c2 = make_summit('C2', 1100, 49.0, 19.5, key_col=make_col(None, 700, 49.0, 19.7),
                             prominence_parent=cls.p)
        # Both ends outside; only the leg down to its key col crosses the view.
        cls.c3 = make_summit('C3', 1100, 49.3, 19.95, key_col=make_col(None, 600, 48.7, 19.95),
                             prominence_parent=cls.p)
        # Far away and linked to nothing near.
        cls.far = make_summit('Far', 1300, 49.0, 30.0, key_col=make_col(None, 500, 49.0, 29.0),
                              prominence_parent=cls.g)
        # In isolation mode, C1 draws towards Far instead.
        cls.c1.isolation_parent, cls.c1.isolation_source = cls.far, fixture_source()
        cls.c1.save()
        # Cols nobody's key col is: one in the view, one outside it.
        cls.free_in = make_col('Free in', 400, 49.05, 20.05)
        cls.free_out = make_col('Free out', 400, 49.05, 21.0)

    def setUp(self):
        refresh_and_record_map_zooms()
        SummitMapZoom.objects.update(z=5, vz_prominence=5, vz_isolation=5, vz_slope=5, vz_horizon=5, vz_line=5)

    def get(self, **params):
        params = {'bbox': VIEW, 'zoom': 10, 'mode': 'prominence', **params}
        response = self.client.get(reverse('map-features'), params)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def summits(self, **params):
        return {f['properties']['name'] for f in self.get(**params)['summits']['features']}

    def test_a_summit_in_view_and_its_parent_outside(self):
        names = self.summits()
        self.assertIn('C1', names)
        self.assertIn('P', names)

    def test_a_child_outside_comes_when_it_shows_at_this_zoom(self):
        self.assertIn('D', self.summits())
        SummitMapZoom.objects.filter(summit=self.d).update(vz_prominence=12)
        self.assertNotIn('D', self.summits())

    def test_a_summit_in_view_that_shows_later_does_not_come(self):
        # with its child: a parent never shows later than its child
        SummitMapZoom.objects.filter(summit__in=(self.c1, self.d)).update(vz_prominence=12)
        self.assertNotIn('C1', self.summits())

    def test_an_edge_crossing_the_view_brings_both_its_ends(self):
        self.assertIn('C2', self.summits())

    def test_a_routed_edge_crossing_only_through_its_col_leg(self):
        self.assertIn('C3', self.summits())

    def test_grandparents_and_strangers_do_not_come(self):
        names = self.summits()
        self.assertNotIn('G', names)
        self.assertNotIn('Far', names)

    def test_each_mode_follows_its_own_parent(self):
        self.assertIn('Far', self.summits(mode='isolation'))
        self.assertNotIn('P', self.summits(mode='isolation'))

    def test_without_a_hierarchy_only_what_is_in_view(self):
        self.assertEqual(self.summits(mode='none'), {'C1'})

    def test_every_summit_carries_its_zoom_in_every_mode(self):
        SummitMapZoom.objects.filter(summit=self.c1).update(z=7, vz_isolation=6)
        c1 = next(f for f in self.get()['summits']['features'] if f['properties']['name'] == 'C1')
        self.assertEqual(c1['properties']['z'], 7)
        self.assertEqual(c1['properties']['zooms'],
                         {'none': 7, 'prominence': 5, 'isolation': 6, 'slope': 5, 'horizon': 5, 'line': 5})

    def test_key_cols_come_with_their_summits_and_free_cols_only_in_view(self):
        cols = {f['properties']['pk'] for f in self.get()['cols']['features']}
        self.assertIn(self.k1.pk, cols)
        self.assertIn(self.free_in.pk, cols)
        self.assertNotIn(self.free_out.pk, cols)

    def test_a_rivers_cols(self):
        river = River.objects.create()
        Col.objects.filter(pk=self.free_out.pk).update(confluence_river=river)
        payload = self.client.get(reverse('map-features'), {'river': river.pk}).json()
        self.assertEqual([f['properties']['pk'] for f in payload['cols']['features']], [self.free_out.pk])
        self.assertEqual(payload['summits']['features'], [])

    def test_bad_requests_are_refused(self):
        url = reverse('map-features')
        for params in ({}, {'bbox': VIEW, 'zoom': 'x'}, {'bbox': VIEW, 'zoom': 10, 'mode': 'slope2'},
                       {'bbox': '1,2,3', 'zoom': 10}, {'river': 'x'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(url, params).status_code, 400)


class MapZoomRefreshTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.top = make_summit('Top', 2000, 49.0, 20.0, island_high_point=True)
        cls.low = make_summit('Low', 1000, 49.05, 20.05, key_col=make_col(None, 800, 49.02, 20.02),
                              prominence_parent=cls.top)

    def request(self):
        return self.client.get(reverse('map-features'), {'bbox': VIEW, 'zoom': 18, 'mode': 'prominence'})

    def refresh(self):
        call_command('refresh_map_zooms', stdout=open('/dev/null', 'w'))

    def test_a_request_never_builds_them(self):
        # a rebuild takes minutes at millions of summits: never inside a request
        self.assertEqual(self.request().status_code, 200)
        self.assertEqual(SummitMapZoom.objects.count(), 0)
        bump_data_version()
        self.request()
        self.assertEqual(SummitMapZoom.objects.count(), 0)

    def test_the_command_builds_them(self):
        self.refresh()
        self.assertEqual(SummitMapZoom.objects.count(), 2)
        self.assertIsNotNone(SummitMapZoom.objects.get(summit=self.low).edge_prominence)

    def test_an_edit_waits_for_the_command(self):
        self.refresh()
        newer = make_summit('Newer', 1500, 49.1, 20.1)
        self.request()
        self.assertFalse(SummitMapZoom.objects.filter(summit=newer).exists())
        self.refresh()
        self.assertTrue(SummitMapZoom.objects.filter(summit=newer).exists())

    def test_a_parent_shows_as_early_as_its_child(self):
        self.refresh()
        top, low = (SummitMapZoom.objects.get(summit=s) for s in (self.top, self.low))
        self.assertLessEqual(top.vz_prominence, low.vz_prominence)

    def test_the_command_leaves_nothing_to_rebuild(self):
        call_command('refresh_map_zooms', stdout=open('/dev/null', 'w'))
        self.assertEqual(built_for(), data_version())
        self.assertEqual(SummitMapZoom.objects.count(), 2)
