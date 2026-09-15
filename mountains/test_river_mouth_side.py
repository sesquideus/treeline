"""
Tests for the bank a river joins its parent on, and for the two summits it names.

The mark is a drawing of the confluence, not an arrow: Unicode has no glyph for "a
confluence with the minor stream on the left", and every arrow that comes close needs a
convention to read it — which is the ambiguous part, since left and right bank are named
looking downstream and that is not the side they fall on in a picture.

So what is pinned here is the wiring rather than a direction: each `mouth_side` reaches its
own sprite, every sprite id the model names exists in the sprite file, and the same ids are
what `static/js/map.js` builds its `<use>` from. A mark pointing at a symbol that is not
there renders as nothing at all — an empty cell, with no error anywhere. The same holds for
the watershed summit the hover highlights: it is looked up by pk in the summit skeleton, so
the two endpoints have to agree about that pk.

What the popup *renders* is not asserted here — `mountains/test_river_popup.py` runs the
real builders and reads the markup. The one source assertion left is the hover, which lives
inside `initGlobalMap`'s closure and needs OpenLayers, so it cannot be reached that way.
"""

import json

from django.contrib.gis.geos import Point
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from mountains.models import River
from mountains.test_factories import make_point, make_summit

SPRITE = 'mountains/templates/mountains/blocks/mouth-side-sprite.html'


class MouthSideMarkTests(TestCase):
    def make_river(self, mouth_side):
        return River(mouth_side=mouth_side)

    def test_each_side_reaches_its_own_sprite(self):
        codes = {side: self.make_river(side).mouth_side_mark()['code'] for side in 'LROS'}
        self.assertEqual(codes, {'L': 'left', 'R': 'right',
                                 'O': 'undecided', 'S': 'sea'})

    def test_every_sprite_the_model_names_exists(self):
        """A `<use>` pointing at a missing symbol draws nothing and reports nothing."""
        with open(SPRITE, encoding='utf-8') as handle:
            sprite = handle.read()
        for code, _ in River.MOUTH_CHOICES:
            mark = self.make_river(code).mouth_side_mark()
            with self.subTest(choice=code):
                self.assertIn(f'id="mouth-side-{mark["code"]}"', sprite)

    def test_every_choice_has_a_mark(self):
        for code, _ in River.MOUTH_CHOICES:
            with self.subTest(choice=code):
                self.assertIsNotNone(self.make_river(code).mouth_side_mark())

    def test_an_unrecorded_side_has_no_mark_at_all(self):
        """`None` is 'nobody looked', which is not the same as 'O' — 'looked, undecidable'."""
        self.assertIsNone(self.make_river(None).mouth_side_mark())
        self.assertEqual(self.make_river(None).mouth_side_abbr(), '')

    def test_the_abbr_carries_the_sprite_and_the_wording(self):
        abbr = self.make_river('L').mouth_side_abbr()
        self.assertIn('href="#mouth-side-left"', abbr)
        self.assertIn('left bank', abbr)
        self.assertIn('downstream', abbr)


class MouthSideRenderingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent = River.objects.create(
            source=make_point('Big source', 900.0, 49.3, 20.9),
            mouth=Point(21.0, 48.7, srid=4326), mouth_altitude=300.0,
            mouth_side='S')
        cls.river = River.objects.create(
            source=make_point('Brook source', 1500.0, 49.05, 20.4),
            mouth=Point(20.6, 48.9, srid=4326), mouth_altitude=500.0,
            parent=cls.parent, mouth_side='L')

    def setUp(self):
        # LocMemCache outlives a test while the database is rolled back after it.
        cache.clear()

    def test_the_detail_page_marks_the_mouth(self):
        html = self.client.get(self.river.get_absolute_url()).content.decode()
        self.assertIn('title="joins from the left bank, looking downstream"', html)
        self.assertIn('href="#mouth-side-left"', html)

    def test_the_detail_page_marks_each_tributary(self):
        html = self.client.get(self.parent.get_absolute_url()).content.decode()
        self.assertIn('<td class="mouth-side">', html)
        self.assertIn('mouth-side-left', html.split('<td class="mouth-side">')[1])

    def test_the_sprite_is_on_the_page(self):
        """`<use href="#...">` resolves only within the same document."""
        html = self.client.get(self.river.get_absolute_url()).content.decode()
        self.assertIn('id="mouth-side-left"', html)

    def test_the_map_payload_carries_the_mark(self):
        """`to_dict()` feeds `to_geojson()`, which is what the popup reads."""
        mark = self.river.to_dict()['mouth_side']
        self.assertEqual(mark['code'], 'left')
        self.assertIn('left bank', mark['title'])
        self.river.mouth_side = None
        self.assertIsNone(self.river.to_dict()['mouth_side'])

    def test_the_rivers_endpoint_carries_the_mark(self):
        response = self.client.get(reverse('rivers-geojson'))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        marks = {f['properties']['name']: f['properties']['mouth_side']
                 for f in payload['features']}
        self.assertEqual(marks['Brook source']['code'], 'left')
        self.assertEqual(marks['Big source']['code'], 'sea')


class RiverWatershedTests(TestCase):
    """
    The two summits a river names — both shown in the map popup, one of them highlighted on
    hover.

    They are different claims *and different kinds of object*: `source_summit` is a Summit,
    the dominant peak above the source; `watershed_high_point` is a NamedPoint, the highest
    ground in the basin, which need not be catalogued as a summit at all. Their pk spaces
    overlap, so confusing the two produces a popup that is wrong rather than empty.
    """

    @classmethod
    def setUpTestData(cls):
        cls.gerlach = make_summit('Gerlach', 2655.0, 49.16, 20.13)
        cls.krivan = make_summit('Kriváň', 2494.0, 49.16, 19.99)
        cls.river = River.objects.create(
            source=make_point('Poprad source', 1500.0, 49.05, 20.4),
            mouth=Point(20.6, 48.9, srid=4326), mouth_altitude=500.0,
            watershed_high_point=cls.gerlach.point, source_summit=cls.krivan)
        cls.bare = River.objects.create(
            source=make_point('Nameless source', 900.0, 49.3, 20.9),
            mouth=Point(21.0, 48.7, srid=4326), mouth_altitude=300.0)

    def setUp(self):
        cache.clear()

    def test_the_payload_names_the_high_point_of_the_watershed(self):
        watershed = self.river.to_dict()['watershed_high_point']
        self.assertEqual(watershed['pk'], self.gerlach.point.pk)
        self.assertEqual(watershed['name'], 'Gerlach')
        self.assertAlmostEqual(watershed['alt'], 2655.0, places=6)

    def test_the_payload_keeps_the_point_and_its_summit_apart(self):
        """
        `pk` addresses `/point/<pk>/`, `summit` addresses the summit skeleton. They are
        different numbers from overlapping sequences, and swapping them yields a working
        link to the wrong mountain.
        """
        watershed = self.river.to_dict()['watershed_high_point']
        self.assertEqual(watershed['pk'], self.gerlach.point.pk)
        self.assertEqual(watershed['summit'], self.gerlach.pk)
        self.assertNotEqual(watershed['pk'], watershed['summit'])

    def test_a_high_point_that_is_no_summit_carries_no_summit_pk(self):
        """The case the whole change exists for."""
        self.river.watershed_high_point = make_point('Bystrá sedlovka', 1842.0, 49.0, 19.8)
        watershed = self.river.to_dict()['watershed_high_point']
        self.assertEqual(watershed['name'], 'Bystrá sedlovka')
        self.assertIsNone(watershed['summit'])

    def test_the_payload_names_the_peak_above_the_source_too(self):
        """Both, and not interchangeably: the popup shows them in different sections."""
        payload = self.river.to_dict()
        self.assertEqual(payload['source_summit']['pk'], self.krivan.pk)
        self.assertEqual(payload['watershed_high_point']['pk'], self.gerlach.point.pk)

    def test_a_river_with_neither_carries_none(self):
        self.assertIsNone(self.bare.to_dict()['watershed_high_point'])
        self.assertIsNone(self.bare.to_dict()['source_summit'])

    def test_a_nameless_high_point_is_described_rather_than_printed_as_none(self):
        """`NamedPoint.name` is nullable, and this field is now open to uncatalogued places."""
        self.river.watershed_high_point = make_point(None, 1200.0, 49.0, 19.8)
        self.assertIn('unnamed', self.river.to_dict()['watershed_high_point']['name'])

    def test_the_endpoint_carries_both(self):
        response = self.client.get(reverse('rivers-geojson'))
        self.assertEqual(response.status_code, 200)
        properties = {f['properties']['name']: f['properties']
                      for f in json.loads(response.content)['features']}
        self.assertEqual(properties['Poprad source']['watershed_high_point']['name'], 'Gerlach')
        self.assertEqual(properties['Poprad source']['source_summit']['name'], 'Kriváň')
        self.assertIsNone(properties['Nameless source']['watershed_high_point'])
        self.assertIsNone(properties['Nameless source']['source_summit'])

    def test_both_landmarks_are_loaded_up_front(self):
        """Without these the popup endpoint is two extra queries per river."""
        rivers = list(River.objects.with_source_summit().with_watershed_high_point())
        with self.assertNumQueries(0):
            for river in rivers:
                river.source_summit and river.source_summit.point.name
                # including the reverse one-to-one that says whether it is a summit
                river.watershed_high_point and getattr(river.watershed_high_point,
                                                       'summit', None)

    def test_the_summit_pk_finds_the_right_summit_in_the_skeleton(self):
        """
        The hover highlight looks `watershed_high_point.summit` up in the skeleton the map
        already holds (`summitsByPk`). Bare membership is not enough to assert: the pk
        spaces overlap, so a wrong number can still be *a* valid key. This checks the
        skeleton entry it finds is actually the same mountain.
        """
        rivers = json.loads(self.client.get(reverse('rivers-geojson')).content)
        summits = json.loads(self.client.get(reverse('summits-geojson')).content)
        skeleton = {f['properties']['pk']: f['properties'] for f in summits['features']}

        named = [f for f in rivers['features']
                 if (f['properties']['watershed_high_point'] or {}).get('summit')]
        self.assertTrue(named, 'the fixture should have a high point that is a summit')
        for feature in named:
            watershed = feature['properties']['watershed_high_point']
            with self.subTest(river=feature['properties']['name']):
                self.assertIn(watershed['summit'], skeleton)
                self.assertEqual(skeleton[watershed['summit']]['name'], watershed['name'])

    def test_the_point_pk_is_not_used_against_the_skeleton(self):
        """
        The trap this design exists to avoid: `pk` is a NamedPoint pk, and feeding it to the
        summit skeleton finds either nothing or the wrong mountain.
        """
        watershed = self.river.to_dict()['watershed_high_point']
        summits = json.loads(self.client.get(reverse('summits-geojson')).content)
        skeleton = {f['properties']['pk']: f['properties'] for f in summits['features']}
        wrong = skeleton.get(watershed['pk'])
        self.assertTrue(wrong is None or wrong['name'] != watershed['name'],
                        'the point pk must not double as a usable summit key')

    def test_hovering_a_river_marks_the_watershed_summit(self):
        """
        Scoped to the river branch of `showConfluenceGroup`, so it cannot pass on a mention
        of the field elsewhere in the file. Python cannot run map.js; this is the most
        the suite can say about the hover itself.
        """
        with open('static/js/map.js', encoding='utf-8') as handle:
            branch = handle.read().split("if (type === 'river') {")[1].split('return;')[0]
        self.assertIn("feature.get('watershed_high_point')", branch)
        self.assertIn('confluencePointSource.addFeature', branch)
        self.assertIn('summitsByPk[watershed.summit]', branch)
