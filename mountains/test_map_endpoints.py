"""
Tests for what the global map loads.

The flat endpoints serve skeletons — position, name, prominence and the ids the browser
joins lineage on — and the popup half is fetched per viewport from `/summits/detail.json`
and `/cols/detail.json`. Two contracts hold that together and neither shows up as an error
when broken, only as a line missing from the map or a blank row in a popup:

- the property names. `static/js/map.js` and `static/js/styles.js` read `prom`, `kc`, `ilp`
  and the `*_parent` ids off the skeleton, and merge the detail payload straight onto the
  same feature, so the two must agree with each other and with `to_dict()`.
- the query string of the detail endpoints, which is also the public boundary: anything
  malformed must be refused rather than reach the ORM.
"""

from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from mountains.models import Col, River, Summit
from mountains.test_factories import make_point
from mountains.test_factories import make_col, make_summit
from mountains.views.cache import data_version

# What map.js reads off a skeleton feature before any detail has arrived.
SUMMIT_SKELETON_KEYS = {'type', 'pk', 'name', 'prom', 'prominence_parent', 'isolation_parent',
                        'slope_parent', 'horizon_parent', 'kc', 'ilp'}
COL_SKELETON_KEYS = {'type', 'pk', 'name', 'confluence'}


class MapDataTestCase(TestCase):
    """
    Two summits a degree apart in the Tatras, plus one far away, so a bbox has something to
    exclude:

        Alpha 3000 m and Beta 2000 m near (49, 20); Distant 1500 m at (0, 0).
    """

    @classmethod
    def setUpTestData(cls):
        cls.alpha_col = make_col('Alpha col', 2500.0, 49.0, 20.5)
        cls.alpha = make_summit('Alpha', 3000.0, 49.0, 20.0, key_col=cls.alpha_col,
                                nearest_higher_point=Point(21.0, 49.0, srid=4326))
        cls.beta = make_summit('Beta', 2000.0, 49.1, 20.1,
                               prominence_parent=cls.alpha, isolation_parent=cls.alpha,
                               isolation_name='western ridge',
                               slope_parent=cls.alpha, horizon_parent=cls.alpha,
                               nearest_higher_point=Point(20.05, 49.05, srid=4326))
        cls.island = make_summit('Island', 1200.0, 49.2, 20.2, island_high_point=True)
        cls.distant = make_summit('Distant', 1500.0, 0.0, 0.0)

        cls.big_river = River.objects.create(
            source=make_point('Big source', 900.0, 49.3, 20.9),
            mouth=Point(21.0, 48.7, srid=4326), mouth_altitude=300.0)
        cls.river = River.objects.create(
            source=make_point('Brook source', 1500.0, 49.05, 20.4),
            mouth=Point(20.6, 48.9, srid=4326), mouth_altitude=500.0,
            parent=cls.big_river)
        cls.river_col = make_col('River col', 1200.0, 49.05, 20.45,
                                 confluence_river=cls.river)

    def setUp(self):
        # LocMemCache outlives a test, while the database is rolled back after it: without
        # this a payload cached by one test could be served to the next.
        cache.clear()

    def features(self, url_name):
        response = self.client.get(reverse(url_name))
        self.assertEqual(response.status_code, 200)
        return {f['properties']['pk']: f for f in response.json()['features']}

    def detail(self, url_name, **params):
        response = self.client.get(reverse(url_name), params)
        self.assertEqual(response.status_code, 200)
        return response.json()


class SkeletonTests(MapDataTestCase):
    def test_summit_skeleton_carries_what_the_client_draws_with(self):
        feature = self.features('summits-geojson')[self.beta.pk]
        self.assertEqual(feature['geometry']['coordinates'], [20.1, 49.1])
        self.assertEqual(set(feature['properties']), SUMMIT_SKELETON_KEYS)
        self.assertEqual(feature['properties']['type'], 'summit')
        self.assertEqual(feature['properties']['prominence_parent'], self.alpha.pk)
        self.assertEqual(feature['properties']['isolation_parent'], self.alpha.pk)

    def test_col_skeleton_carries_the_confluence_position(self):
        features = self.features('cols-geojson')
        on_a_river = features[self.river_col.pk]['properties']
        self.assertEqual(set(on_a_river), COL_SKELETON_KEYS)
        self.assertEqual(on_a_river['type'], 'col')
        # The position, for the pink line the skeleton alone can draw, and the river, which
        # is what makes the sister cols findable in the browser.
        self.assertEqual(on_a_river['confluence'],
                         {'lon': 20.6, 'lat': 48.9, 'river': self.river.pk})
        self.assertIsNone(features[self.alpha_col.pk]['properties']['confluence'])

    def test_key_col_is_the_id_the_client_joins_on(self):
        features = self.features('summits-geojson')
        self.assertEqual(features[self.alpha.pk]['properties']['kc'], self.alpha_col.pk)
        self.assertIsNone(features[self.beta.pk]['properties']['kc'])

    def test_prominence_matches_the_annotation(self):
        features = self.features('summits-geojson')
        self.assertEqual(features[self.alpha.pk]['properties']['prom'], 500.0)
        # An island high point has no col, so its prominence is its altitude — a plain
        # subtraction would leave it null and drop it out of the styling bands.
        self.assertEqual(features[self.island.pk]['properties']['prom'], 1200.0)
        self.assertIsNone(features[self.beta.pk]['properties']['prom'])

    def test_the_nearest_higher_point_carries_only_its_position(self):
        ilp = self.features('summits-geojson')[self.beta.pk]['properties']['ilp']
        self.assertEqual(ilp, {'lon': 20.05, 'lat': 49.05})

    def test_a_summit_without_a_location_is_skipped(self):
        orphan = Summit.objects.create(point=None)
        self.assertNotIn(orphan.pk, self.features('summits-geojson'))


class DetailKeyTests(MapDataTestCase):
    def test_summit_detail_agrees_with_to_dict_where_they_overlap(self):
        detail, full = self.beta.to_detail_dict(), self.beta.to_dict()
        shared = set(detail) & set(full)
        self.assertEqual(shared, {'name', 'alt', 'countries', 'key_col', 'parent', 'ilp'})
        for key in sorted(shared):
            with self.subTest(key=key):
                self.assertEqual(detail[key], full[key])

    def test_summit_detail_adds_the_hierarchies_to_dict_does_not_carry(self):
        # `slope` / `horizon` rather than `slope_parent` / `horizon_parent`: those names are
        # the parent ids in the skeleton, and the client joins its lineage lines on them.
        self.assertEqual(set(self.beta.to_detail_dict()) - set(self.beta.to_dict()),
                         {'nhn', 'slope', 'horizon'})

    def test_col_detail_is_to_dict_without_the_geometry(self):
        col = Col.objects.with_point().with_minor().get(pk=self.alpha_col.pk)
        self.assertEqual(set(col.to_detail_dict()), set(col.to_dict()) - {'lat', 'lon'})

    def test_the_confluence_carries_the_river_in_both_payloads(self):
        """
        The detail payload is merged onto the skeleton feature, key by key, so a key the
        skeleton has and the detail lacks would be *lost* on the merge — and with it the
        sister cols a hovered col lights up, since they are the cols sharing this id.
        """
        skeleton = self.features('cols-geojson')[self.river_col.pk]['properties']['confluence']
        detail = (self.detail('cols-detail-json', pks=str(self.river_col.pk))
                  [str(self.river_col.pk)]['confluence'])
        self.assertEqual(skeleton['river'], self.river.pk)
        self.assertEqual(detail['river'], self.river.pk)
        self.assertLessEqual(set(skeleton), set(detail))

    def test_col_detail_names_the_river_and_the_one_it_flows_into(self):
        payload = self.detail('cols-detail-json', pks=str(self.river_col.pk))
        river = payload[str(self.river_col.pk)]['river']
        self.assertEqual((river['name'], river['parent']), ('Brook source', 'Big source'))
        self.assertEqual((river['pk'], river['parent_pk']),
                         (self.river.pk, self.big_river.pk))

    def test_summit_detail_describes_the_way_to_the_key_col_and_the_parent(self):
        payload = self.detail('summits-detail-json', pks=str(self.beta.pk))
        beta = payload[str(self.beta.pk)]
        self.assertEqual(beta['alt'], 2000.0)
        self.assertEqual(beta['parent']['name'], 'Alpha')
        self.assertGreater(beta['parent']['rise'], 0)          # the way up is positive
        self.assertEqual(beta['ilp']['parent'], 'Alpha')
        alpha = self.detail('summits-detail-json', pks=str(self.alpha.pk))[str(self.alpha.pk)]
        self.assertEqual(alpha['key_col']['drop'], 500.0)
        self.assertLess(alpha['key_col']['slope'], 0)          # the way down is negative

    def test_summit_detail_describes_the_other_three_hierarchies(self):
        beta = self.detail('summits-detail-json', pks=str(self.beta.pk))[str(self.beta.pk)]
        for section in ('nhn', 'slope', 'horizon'):
            with self.subTest(section=section):
                self.assertEqual(beta[section]['pk'], self.alpha.pk)
                self.assertEqual(beta[section]['name'], 'Alpha')
                self.assertGreater(beta[section]['dist'], 0)
        # The neighbour is measured peak to peak, the isolation to the ground short of it.
        self.assertGreater(beta['nhn']['dist'], beta['ilp']['dist'])
        self.assertEqual(beta['slope']['rise'], 1000.0)
        self.assertGreater(beta['slope']['slope'], 0)          # the way up is positive
        self.assertIsNotNone(beta['horizon']['angle'])          # radians, refraction-free

    def test_the_annotated_and_the_python_paths_agree(self):
        """
        The dict builders read the with_* annotations when a queryset carries them and
        recompute in Python when it does not; the two must not disagree.
        """
        annotated = (Summit.objects.with_slope_parent().with_horizon_parent()
                     .with_isolation().get(pk=self.beta.pk))
        bare = Summit.objects.get(pk=self.beta.pk)
        for builder in ('nhn_dict', 'slope_parent_dict', 'horizon_parent_dict'):
            with self.subTest(builder=builder):
                first, second = getattr(annotated, builder)(), getattr(bare, builder)()
                self.assertEqual(set(first), set(second))
                for key, value in first.items():
                    if isinstance(value, float):
                        self.assertAlmostEqual(value, second[key], places=6)
                    else:
                        self.assertEqual(value, second[key])


class ViewportTests(MapDataTestCase):
    BBOX = '19.0,48.5,21.0,49.5'

    def test_bbox_returns_the_summits_inside_it(self):
        payload = self.detail('summits-detail-json', bbox=self.BBOX)
        self.assertIn(str(self.alpha.pk), payload)
        self.assertNotIn(str(self.distant.pk), payload)

    def test_bbox_returns_the_cols_inside_it(self):
        payload = self.detail('cols-detail-json', bbox=self.BBOX)
        self.assertIn(str(self.alpha_col.pk), payload)

    def test_without_a_bbox_the_whole_world_is_in_view(self):
        payload = self.detail('summits-detail-json')
        self.assertIn(str(self.distant.pk), payload)

    def test_the_limit_keeps_the_most_prominent(self):
        payload = self.detail('summits-detail-json', bbox=self.BBOX, limit=1)
        # Island's 1200 m beats Alpha's 500 m — and Beta, whose prominence is unknown for
        # want of a key col, must not take the place by sorting first as a NULL.
        self.assertEqual(list(payload), [str(self.island.pk)])

    def test_an_unknown_prominence_sorts_last(self):
        payload = self.detail('summits-detail-json', bbox=self.BBOX, limit=2)
        self.assertNotIn(str(self.beta.pk), payload)

    def test_pks_ignores_the_bbox(self):
        payload = self.detail('summits-detail-json', pks=str(self.distant.pk), bbox=self.BBOX)
        self.assertEqual(list(payload), [str(self.distant.pk)])

    def test_malformed_parameters_are_refused(self):
        for params in ({'bbox': '1,2,3'}, {'bbox': 'a,b,c,d'}, {'bbox': '200,0,300,10'},
                       {'limit': 'x'}, {'pks': '1;2'}):
            with self.subTest(params=params):
                response = self.client.get(reverse('summits-detail-json'), params)
                self.assertEqual(response.status_code, 400)


class CacheTests(MapDataTestCase):
    def test_a_save_invalidates_the_cached_payload(self):
        before = self.features('summits-geojson')[self.alpha.pk]['properties']['prom']
        version = data_version()

        self.alpha_col.point.altitude = 2000.0
        self.alpha_col.point.save()

        self.assertGreater(data_version(), version)
        after = self.features('summits-geojson')[self.alpha.pk]['properties']['prom']
        self.assertEqual((before, after), (500.0, 1000.0))
