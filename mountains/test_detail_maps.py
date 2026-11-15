"""
The maps on the summit, col and river pages: what each one shows, and what it is framed on.

Every detail map used to open blank. `makeMap()` built its view with a centre and no zoom,
and waited for the vector source's `change` event to fit the data — but the features were
handed to the source in its constructor, so that event had already passed and never came,
and a view with no zoom has no resolution, so OpenLayers drew nothing at all. The col page
had no map, and the river page an empty pane.

Each page now hands its map a collection in which the features to keep in view carry
`frame: true`, and `fitToFramed()` zooms to those: the object itself and what it is
directly related to. An isolation circle is deliberately not framed — it can be hundreds of
kilometres across, and would shrink everything that matters to one dot.
"""

import json
import re

from django.test import SimpleTestCase, TestCase

from mountains.models import River, Summit
from mountains.test_factories import fixture_source, make_col, make_point, make_summit
from mountains.test_js_runtime import evaluate, feature_expression
from mountains.views.detail_map import summit_features, summit_map_queryset


def map_data(response):
    """The collection a detail page embeds for its map."""
    match = re.search(r'<script id="map-data" type="application/json">(.*?)</script>',
                      response.content.decode(), re.S)
    assert match, 'the page embeds no map data'
    return json.loads(match.group(1))


def kinds(collection, framed=None):
    """The feature types, optionally only those framed (True) or not (False)."""
    return sorted(f['properties']['type'] for f in collection['features']
                  if framed is None or bool(f['properties'].get('frame')) is framed)


class SummitMapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent = make_summit('Parent', 2600, 49.1, 20.1)
        cls.col = make_col('The Col', 1800, 48.98, 20.05)
        cls.summit = make_summit('Summit', 2400, 49.0, 20.0,
                                 key_col=cls.col, prominence_parent=cls.parent)
        cls.summit.nearest_higher_point = cls.parent.point.location
        cls.summit.isolation_parent = cls.parent
        cls.summit.isolation_source = cls.parent.prominence_source or fixture_source()
        cls.summit.save()
        cls.child_col = make_col('Child col', 1500, 49.2, 20.3)
        cls.child = make_summit('Child', 1900, 49.3, 20.4,
                                key_col=cls.child_col, prominence_parent=cls.summit)

    def collection(self):
        response = self.client.get(f'/summit/{self.summit.pk}/')
        self.assertEqual(response.status_code, 200)
        return map_data(response)

    def test_it_frames_the_summit_its_key_col_its_parent_and_its_children(self):
        """Two cols: the summit's own, and the child's."""
        self.assertEqual(kinds(self.collection(), framed=True),
                         ['col', 'col', 'lit_parent', 'prominence_child', 'summit'])

    def cols(self):
        return {f['properties']['pk']: f for f in self.collection()['features']
                if f['properties']['type'] == 'col'}

    def test_a_child_s_key_col_is_the_global_map_s_col(self):
        """
        Serialized by `Col.to_dict()`, as the global map's col layer is, so it gets the same
        marker, the same popup, and the confluence line on hover.
        """
        col = self.cols()[self.child_col.pk]['properties']
        self.assertEqual(col['key_for_pk'], self.child.pk)
        self.assertEqual(col['major_pk'], self.summit.pk)
        self.assertEqual(col['depth'], 400)         # the child's prominence, 1900 − 1500

    def test_both_cols_carry_the_same_properties(self):
        own, childs = (self.cols()[pk]['properties'] for pk in (self.col.pk, self.child_col.pk))
        self.assertEqual(set(own), set(childs))

    def test_a_child_carries_everything_the_global_popup_shows(self):
        """
        The skeleton and the viewport detail, merged as the global map merges them — the
        same keys as the summit itself, so `popupHtml()` gives it the same popup.
        """
        features = self.collection()['features']
        child = next(f for f in features if f['properties']['type'] == 'prominence_child')
        summit = next(f for f in features if f['properties']['type'] == 'summit')
        self.assertEqual(set(child['properties']), set(summit['properties']) - {'subject'})
        for key in ('key_col', 'parent', 'nhn', 'slope', 'horizon', 'ilp', 'countries'):
            self.assertIn(key, child['properties'])
        self.assertEqual(child['properties']['parent']['name'], 'Summit')
        self.assertEqual(child['properties']['key_col']['name'], 'Child col')

    def test_its_popup_is_the_summit_popup(self):
        features = self.collection()['features']
        child = next(f for f in features if f['properties']['type'] == 'prominence_child')
        as_summit = dict(child['properties'], type='summit')
        # `getGeometry()` too: the popup's position row reads it. And `prominenceBand()`,
        # which lives in styles.js and is not loaded with map.js here — for the one call.
        popup = lambda properties: evaluate('''(function () {
            globalThis.prominenceBand = () => ({ colour: '#000', label: 'band' });
            try {
                return popupHtml({ get: key => (%s)[key], getGeometry: () => null });
            } finally {
                delete globalThis.prominenceBand;
            }
        })()''' % json.dumps(properties))
        self.assertEqual(popup(child['properties']), popup(as_summit))
        self.assertIn(f'href="/summit/{self.child.pk}/"', popup(child['properties']))
        self.assertEqual(evaluate('detailUrl(%s)' % feature_expression(child['properties'])),
                         f'/summit/{self.child.pk}/')

    def test_the_nearest_higher_neighbour_is_a_peak_with_the_summit_popup(self):
        features = self.collection()['features']
        neighbour = next(f for f in features if f['properties']['type'] == 'isolation_parent')
        summit = next(f for f in features if f['properties']['type'] == 'summit')
        self.assertEqual(neighbour['properties']['pk'], self.parent.pk)
        self.assertEqual(set(neighbour['properties']), set(summit['properties']) - {'subject'})
        self.assertFalse(neighbour['properties']['frame'])
        self.assertEqual(evaluate('detailUrl(%s)' % feature_expression(neighbour['properties'])),
                         f'/summit/{self.parent.pk}/')

    def test_the_child_cols_cost_no_query_apiece(self):
        """
        Each child's col, its countries and its rivers come with the children: once the
        summit is loaded, only the nearest higher neighbour is fetched, never the children.
        """
        summit = summit_map_queryset().get(pk=self.summit.pk)
        summit.isolation_parent = None      # the one thing fetched apart, not under test
        with self.assertNumQueries(0):
            features = summit_features(summit)
        self.assertEqual(sorted(f['properties']['type'] for f in features
                                if f['properties']['type'] in ('col', 'prominence_child')),
                         ['col', 'col', 'prominence_child'])

    def test_the_isolation_circle_is_drawn_but_not_framed(self):
        collection = self.collection()
        self.assertIn('isolation_circle', kinds(collection))
        self.assertNotIn('isolation_circle', kinds(collection, framed=True))

    def test_a_child_s_climb_runs_through_its_key_col_to_the_summit(self):
        line = next(f for f in self.collection()['features']
                    if f['properties']['type'] == 'child_line')
        self.assertEqual(line['geometry']['coordinates'],
                         [[20.4, 49.3], [20.3, 49.2], [20.0, 49.0]])


    def test_an_empty_nearest_higher_point_is_no_nearest_higher_point(self):
        """
        `POINT EMPTY` is stored for a dozen summits. GeoDjango reads it as a Point that is
        falsy and has no coordinates; treated as present, `.x` raised and the page was a 500.
        """
        from django.contrib.gis.geos import Point
        Summit.objects.filter(pk=self.summit.pk).update(nearest_higher_point=Point(srid=4326))
        response = self.client.get(f'/summit/{self.summit.pk}/')
        self.assertEqual(response.status_code, 200)
        types = kinds(map_data(response))
        self.assertNotIn('isolation_point', types)
        self.assertNotIn('isolation_circle', types)
        self.assertIn('isolation_parent', types)        # the neighbour is still known

    def test_the_json_endpoint_serves_the_same_framing(self):
        """The comparison of old and new: the endpoint is still there, and agrees."""
        served = self.client.get(f'/summit/{self.summit.pk}/geo.json/').json()
        self.assertEqual(kinds(served, framed=True), kinds(self.collection(), framed=True))


class ColMapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent = make_summit('Parent', 2600, 49.1, 20.1)
        cls.col = make_col('The Col', 1800, 48.98, 20.05)
        cls.summit = make_summit('Summit', 2400, 49.0, 20.0,
                                 key_col=cls.col, prominence_parent=cls.parent)

    def test_it_frames_the_col_and_both_summits_it_separates(self):
        collection = map_data(self.client.get(f'/col/{self.col.pk}/'))
        self.assertEqual(kinds(collection, framed=True),
                         ['col', 'key_for_summit', 'lit_parent'])

    def test_the_climb_is_routed_through_the_col(self):
        """Three points, the col in the middle — what the route toggle straightens."""
        collection = map_data(self.client.get(f'/col/{self.col.pk}/'))
        line = next(f for f in collection['features']
                    if f['properties']['type'] == 'prominence_line')
        self.assertEqual(line['geometry']['coordinates'],
                         [[20.0, 49.0], [20.05, 48.98], [20.1, 49.1]])

    def test_a_col_nobody_hangs_off_is_shown_alone(self):
        lonely = make_col(None, 900, 48.5, 19.5)
        collection = map_data(self.client.get(f'/col/{lonely.pk}/'))
        self.assertEqual(kinds(collection), ['col'])


class RiverMapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peak = make_summit('Peak', 1477, 48.77, 20.20)
        cls.high_point = make_point('High point', 1500, 48.8, 20.3)
        cls.river = River.objects.create(
            source=make_point('Stream', 1300, 48.77, 20.21),
            mouth=make_point('Mouth', 100, 47.94, 21.11),
            source_summit=cls.peak, watershed_high_point=cls.high_point)

    def collection(self, river=None):
        response = self.client.get(f'/river/{(river or self.river).pk}/')
        self.assertEqual(response.status_code, 200)
        return map_data(response)

    def test_it_frames_the_whole_course_and_both_landmarks(self):
        self.assertEqual(kinds(self.collection(), framed=True),
                         ['river', 'source_summit', 'watershed_high_point'])

    def test_the_course_runs_from_source_to_mouth(self):
        course = next(f for f in self.collection()['features']
                      if f['properties']['type'] == 'river')
        coordinates = course['geometry']['coordinates']
        self.assertEqual((coordinates[0], coordinates[-1]), ([20.21, 48.77], [21.11, 47.94]))

    def test_a_watershed_high_point_that_is_no_summit_links_to_the_point(self):
        mark = next(f for f in self.collection()['features']
                    if f['properties']['type'] == 'watershed_high_point')
        self.assertEqual(mark['properties']['url'], f'/point/{self.high_point.pk}/')

    def test_a_river_with_only_a_source_is_shown_as_that_point(self):
        stub = River.objects.create(source=make_point('Spring', 900, 48.6, 20.0))
        self.assertEqual(kinds(self.collection(stub), framed=True), ['river_source'])

    def test_it_offers_no_col_routing(self):
        self.assertNotContains(self.client.get(f'/river/{self.river.pk}/'), 'toggle-col')


#: Just enough of `ol.extent` to run `fitToFramed()`: extents are [minX, minY, maxX, maxY].
FAKE_OL = '''{ extent: {
    createEmpty: () => [Infinity, Infinity, -Infinity, -Infinity],
    extend: (e, o) => { e[0] = Math.min(e[0], o[0]); e[1] = Math.min(e[1], o[1]);
                        e[2] = Math.max(e[2], o[2]); e[3] = Math.max(e[3], o[3]); return e; },
    isEmpty: e => e[2] < e[0] || e[3] < e[1],
} }'''


def fitted(features, map_bottom=900, window_height=900):
    """What `fitToFramed()` asks the view to fit, run for real against a stand-in map."""
    return evaluate('''(function () {
        const savedOl = ol, savedWindow = window;
        ol = %s;
        window = { innerHeight: %d };
        let fit = null;
        const map = {
            getView: () => ({ fit: (extent, options) => { fit = { extent, options }; } }),
            getTargetElement: () => ({ getBoundingClientRect: () => ({ bottom: %d }) }),
        };
        const features = %s.map(([props, extent]) => ({
            get: key => props[key], getGeometry: () => ({ getExtent: () => extent }),
        }));
        try {
            fitToFramed(map, { getFeatures: () => features });
            return fit;
        } finally {
            ol = savedOl;
            window = savedWindow;
        }
    })()''' % (FAKE_OL, window_height, map_bottom, json.dumps(features)))


class FitTests(SimpleTestCase):
    SUMMIT = [{'frame': True}, [10, 10, 10, 10]]
    PARENT = [{'frame': True}, [30, 20, 30, 20]]
    CIRCLE = [{}, [-500, -500, 500, 500]]

    def test_only_the_framed_features_decide_the_view(self):
        self.assertEqual(fitted([self.SUMMIT, self.CIRCLE, self.PARENT])['extent'],
                         [10, 10, 30, 20])

    def test_with_nothing_framed_it_fits_everything(self):
        """The comparison map marks nothing, and keeps fitting its whole collection."""
        self.assertEqual(fitted([[{}, [0, 0, 1, 1]], [{}, [5, 5, 6, 6]]])['extent'],
                         [0, 0, 6, 6])

    def test_a_lone_point_is_not_fitted_at_the_closest_zoom(self):
        self.assertEqual(fitted([self.SUMMIT])['options']['maxZoom'], 14)

    def test_the_part_of_the_map_below_the_fold_is_left_out(self):
        """A one-screen map starting below the header has its bottom under the fold."""
        padding = fitted([self.SUMMIT], map_bottom=961, window_height=900)['options']['padding']
        self.assertEqual(padding, [60, 60, 121, 60])

    def test_an_empty_collection_leaves_the_view_alone(self):
        self.assertIsNone(fitted([]))

    def test_make_map_no_longer_waits_for_a_change_that_never_comes(self):
        with open('static/js/map.js', encoding='utf-8') as handle:
            source = handle.read()
        make_map = source.split('function makeMap(')[1].split('\nfunction ')[0]
        self.assertNotIn("once('change'", make_map)
        self.assertIn('fitToFramed(map', make_map)


class ColRiversTests(TestCase):
    """The minor river whose mouth is a col's confluence, and the major one it joins."""

    @classmethod
    def setUpTestData(cls):
        cls.major = River.objects.create(source=make_point('Major', 1200, 49.5, 20.5),
                                         mouth=make_point('Sea', 0, 52.0, 21.0))
        cls.minor = River.objects.create(source=make_point('Minor', 1000, 49.0, 20.0),
                                         mouth=make_point('Junction', 300, 50.0, 20.8),
                                         parent=cls.major)
        cls.col = make_col('The Col', 800, 49.2, 20.2, confluence_river=cls.minor)

    def rivers(self):
        collection = map_data(self.client.get(f'/col/{self.col.pk}/'))
        return {f['properties']['role']: f for f in collection['features']
                if f['properties']['type'] == 'river'}

    def test_both_rivers_are_drawn(self):
        rivers = self.rivers()
        self.assertEqual(sorted(rivers), ['major', 'minor'])
        self.assertEqual(rivers['minor']['properties']['pk'], self.minor.pk)
        self.assertEqual(rivers['major']['properties']['pk'], self.major.pk)

    def test_the_major_river_runs_through_the_junction(self):
        """The minor river's mouth is a waypoint on the major one's course."""
        self.assertIn([20.8, 50.0], self.rivers()['major']['geometry']['coordinates'])

    def test_neither_widens_the_view(self):
        self.assertFalse(any(f['properties'].get('frame') for f in self.rivers().values()))

    def test_a_col_with_no_confluence_has_no_rivers(self):
        dry = make_col('Dry', 700, 48.0, 19.0)
        collection = map_data(self.client.get(f'/col/{dry.pk}/'))
        self.assertNotIn('river', kinds(collection))


class ParentMarkTests(TestCase):
    """
    The parent on a summit's or col's map: lit, as the global map lights it on hover, and
    a way to its own page. It was neither — a plain blue triangle with no link, and on the
    summit page often hidden under the encirclement parent's red one, which is so often
    the same peak, and which took the click without having a page to open.
    """

    @classmethod
    def setUpTestData(cls):
        cls.grand = make_summit('Grand', 3000, 49.3, 20.3)
        cls.parent = make_summit('Parent', 2600, 49.1, 20.1,
                                 key_col=make_col('Low col', 1000, 49.2, 20.2),
                                 prominence_parent=cls.grand)
        cls.col = make_col('The Col', 1800, 48.98, 20.05)
        cls.summit = make_summit('Summit', 2400, 49.0, 20.0,
                                 key_col=cls.col, prominence_parent=cls.parent)

    def features(self, path):
        return map_data(self.client.get(path))['features']

    def parent_mark(self, path):
        return next(f for f in self.features(path) if f['properties']['type'] == 'lit_parent')

    def test_it_links_to_the_parent_s_page_from_both_maps(self):
        for path in (f'/summit/{self.summit.pk}/', f'/col/{self.col.pk}/'):
            with self.subTest(path=path):
                properties = self.parent_mark(path)['properties']
                self.assertEqual(properties['url'], f'/summit/{self.parent.pk}/')
                self.assertEqual(properties['prom'], 1600)

    def test_a_click_on_it_opens_that_page(self):
        properties = self.parent_mark(f'/summit/{self.summit.pk}/')['properties']
        self.assertEqual(evaluate('detailUrl(%s)' % feature_expression(properties)),
                         f'/summit/{self.parent.pk}/')

    def test_hovering_it_names_it(self):
        properties = self.parent_mark(f'/col/{self.col.pk}/')['properties']
        popup = evaluate('popupHtml(%s)' % feature_expression(properties))
        self.assertIn(f'href="/summit/{self.parent.pk}/"', popup)
        self.assertIn('Parent', popup)

    def test_an_encirclement_parent_that_is_the_parent_gets_no_marker_on_top(self):
        """Parent's key col (1000 m) is below the summit's (1800 m): it encircles it."""
        self.assertEqual(self.summit.compute_encirclement_parent(), self.parent)
        types = [f['properties']['type'] for f in self.features(f'/summit/{self.summit.pk}/')]
        self.assertNotIn('encirclement_parent', types)
        self.assertIn('encirclement_line', types)

    def test_a_different_encirclement_parent_has_its_own_link(self):
        self.parent.key_col.point.altitude = 2000      # now above the summit's col
        self.parent.key_col.point.save()
        self.grand.key_col = make_col('Lowest col', 500, 49.4, 20.4)    # and this below it
        self.grand.save()
        mark = next(f for f in self.features(f'/summit/{self.summit.pk}/')
                    if f['properties']['type'] == 'encirclement_parent')
        self.assertEqual(mark['properties']['url'], f'/summit/{self.grand.pk}/')

    def test_a_col_beside_it_stays_reachable(self):
        """
        A key col is often a few pixels from its parent, whose lit triangle is several times
        wider; drawn under it, the col could be neither hovered nor clicked.
        """
        styles = 'static/js/styles.js'
        self.assertGreater(evaluate('Z_COL', source=styles),
                           evaluate('Z_POINT + Z_SUMMIT_SPAN + 1', source=styles))

    def test_the_page_s_own_summit_is_drawn_above_the_cols(self):
        features = map_data(self.client.get(f'/summit/{self.summit.pk}/'))['features']
        subjects = [f['properties']['pk'] for f in features if f['properties'].get('subject')]
        self.assertEqual(subjects, [self.summit.pk])
        styles = 'static/js/styles.js'
        self.assertGreater(evaluate('Z_SUBJECT', source=styles), evaluate('Z_COL', source=styles))

    def test_it_is_drawn_above_every_summit_marker(self):
        """The marker on top is the one a hit test returns, and so the one a click opens."""
        self.assertGreater(evaluate('Z_POINT + Z_SUMMIT_SPAN + 1', source='static/js/styles.js'),
                           evaluate('prominenceZIndex(1e9)', source='static/js/styles.js'))
