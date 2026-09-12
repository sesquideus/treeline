"""
Tests for the bank a river joins its parent on.

`River.mouth_side` has been on the model and in the admin since migration 0026 without
ever being displayed. It is now an arrow on the river pages and in the map popup, and the
one thing worth pinning is which way round the arrows go: left and right bank are named
looking *downstream*, so on a page where the main river runs downwards a left-bank
tributary arrives from the right and curves left. Getting that backwards is invisible —
the page still renders, it just tells the reader the opposite of the truth.
"""

import json

from django.contrib.gis.geos import Point
from django.test import TestCase
from django.urls import reverse

from mountains.models import River
from mountains.test_factories import make_point

LEFT, RIGHT, UNDECIDED, SEA = '↲', '↳', '↓', '≋'


class MouthSideMarkTests(TestCase):
    def make_river(self, mouth_side):
        return River(mouth_side=mouth_side)

    def test_left_bank_curves_left(self):
        """Looking downstream, so it comes in from the page's right — not the other way."""
        self.assertEqual(self.make_river('L').mouth_side_mark()['symbol'], LEFT)

    def test_right_bank_curves_right(self):
        self.assertEqual(self.make_river('R').mouth_side_mark()['symbol'], RIGHT)

    def test_the_two_banks_are_mirror_images(self):
        marks = {side: self.make_river(side).mouth_side_mark()['symbol'] for side in 'LR'}
        self.assertNotEqual(marks['L'], marks['R'])

    def test_an_undecided_bank_and_the_sea_are_each_their_own_mark(self):
        self.assertEqual(self.make_river('O').mouth_side_mark()['symbol'], UNDECIDED)
        self.assertEqual(self.make_river('S').mouth_side_mark()['symbol'], SEA)

    def test_every_choice_has_a_mark(self):
        for code, _ in River.MOUTH_CHOICES:
            with self.subTest(choice=code):
                self.assertIsNotNone(self.make_river(code).mouth_side_mark())

    def test_an_unrecorded_side_has_no_mark_at_all(self):
        """`None` is 'nobody looked', which is not the same as 'O' — 'looked, undecidable'."""
        self.assertIsNone(self.make_river(None).mouth_side_mark())
        self.assertEqual(self.make_river(None).mouth_side_abbr(), '')

    def test_the_abbr_carries_the_wording(self):
        abbr = self.make_river('L').mouth_side_abbr()
        self.assertIn(LEFT, abbr)
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

    def test_the_detail_page_marks_the_mouth(self):
        html = self.client.get(self.river.get_absolute_url()).content.decode()
        self.assertIn(f'title="joins from the left bank, looking downstream">{LEFT}</abbr>', html)

    def test_the_detail_page_marks_each_tributary(self):
        html = self.client.get(self.parent.get_absolute_url()).content.decode()
        self.assertIn('<td class="mouth-side">', html)
        self.assertIn(LEFT, html.split('<td class="mouth-side">')[1])

    def test_the_map_payload_carries_the_mark(self):
        """`to_dict()` feeds `to_geojson()`, which is what the popup reads."""
        mark = self.river.to_dict()['mouth_side']
        self.assertEqual(mark['symbol'], LEFT)
        self.assertIn('left bank', mark['title'])
        self.river.mouth_side = None
        self.assertIsNone(self.river.to_dict()['mouth_side'])

    def test_the_rivers_endpoint_carries_the_mark(self):
        response = self.client.get(reverse('rivers-geojson'))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        marks = {f['properties']['name']: f['properties']['mouth_side']
                 for f in payload['features']}
        self.assertEqual(marks['Brook source']['symbol'], LEFT)
        self.assertEqual(marks['Big source']['symbol'], SEA)
