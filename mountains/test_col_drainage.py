"""
Where a drop on either side of a col goes (`mountains/models/drainage.py`).

The river tree here:

    Ocean (the root)
    └── Sea                    mouth   0 m
        ├── P                  mouth 100 m
        │   ├── Z              mouth 300 m   joins P below X
        │   ├── X              mouth 500 m   the confluence river
        │   │   └── B1         mouth 800 m
        │   └── Y              mouth 600 m   joins P above X
        └── Other              mouth  50 m   never reaches P
    Frag                       mouth 900 m   not yet connected: a gap, not a second root
    └── Creek                  mouth 950 m
"""
from django.core.exceptions import ValidationError
from django.test import TestCase

from mountains.models import Col, River
from mountains.models.drainage import ERROR, HINT, UNVERIFIABLE, RiverIndex, analyse
from mountains.test_factories import make_col, make_point


def river(name, parent=None, mouth=None):
    return River.objects.create(
        source=make_point(name, (mouth or 0) + 200, 49.0, 20.0),
        mouth=make_point(f'{name} mouth', mouth, 49.0, 20.1) if mouth is not None else None,
        parent=parent)


class DrainageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ocean = river('Ocean')
        cls.sea = river('Sea', cls.ocean, 0)
        cls.p = river('P', cls.sea, 100)
        cls.z = river('Z', cls.p, 300)
        cls.x = river('X', cls.p, 500)
        cls.b1 = river('B1', cls.x, 800)
        cls.y = river('Y', cls.p, 600)
        cls.other = river('Other', cls.sea, 50)
        cls.frag = river('Frag', None, 900)
        cls.creek = river('Creek', cls.frag, 950)

    def drain(self, confluence=None, a=None, b=None):
        col = Col(confluence_river=confluence, river_a=a, river_b=b)
        return analyse(col, RiverIndex())

    def levels(self, drainage):
        return [f.level for f in drainage.findings]

    def chains(self, drainage):
        return [(s.role, [River.objects.get(pk=pk).source.name for pk in s.chain], s.end)
                for s in drainage.sides]


class IndexTests(DrainageTestCase):
    def test_the_root_is_the_ocean_and_a_fragment_is_a_gap(self):
        index = RiverIndex()
        self.assertEqual(index.root, self.ocean.pk)
        self.assertEqual(index.end(index.chain(self.creek.pk)), 'gap')
        self.assertEqual(index.end(index.chain(self.b1.pk)), 'sea')


class WithConfluenceTests(DrainageTestCase):
    def test_coarse_only_the_confluence(self):
        d = self.drain(self.x)
        self.assertEqual((d.meet, d.meet_river, d.meet_into), ('mouth', self.x.pk, self.p.pk))
        self.assertEqual(d.sides, [])
        self.assertEqual(d.findings, [])

    def test_both_sides_run_down_to_the_junction_tributary_first(self):
        d = self.drain(self.x, a=self.y, b=self.b1)
        self.assertEqual(self.chains(d), [('tributary', ['B1', 'X'], 'target'),
                                          ('main', ['Y', 'P'], 'target')])
        self.assertEqual(d.findings, [])

    def test_the_main_side_may_be_the_parent_itself(self):
        d = self.drain(self.x, a=self.x, b=self.p)
        self.assertEqual(self.chains(d), [('tributary', ['X'], 'target'), ('main', ['P'], 'target')])
        self.assertEqual(d.findings, [])

    def test_a_main_side_joining_below_the_confluence_is_an_error(self):
        d = self.drain(self.x, a=self.b1, b=self.z)
        self.assertEqual(self.levels(d), [ERROR])
        self.assertIn('not above the mouth of X', d.findings[0].message)
        self.assertEqual(d.findings[0].field, 'river_b')

    def test_both_sides_through_the_confluence_river_is_an_error(self):
        d = self.drain(self.x, a=self.b1, b=self.x)
        self.assertEqual(self.levels(d), [ERROR])
        self.assertIn('meet above its mouth', d.findings[0].message)

    def test_a_side_that_reaches_the_sea_elsewhere_is_an_error(self):
        d = self.drain(self.x, a=self.b1, b=self.other)
        self.assertEqual(self.levels(d), [ERROR])
        self.assertEqual(self.chains(d)[1], (None, ['Other', 'Sea', 'Ocean'], 'sea'))

    def test_the_same_river_on_both_sides_contradicts_a_confluence(self):
        d = self.drain(self.x, a=self.b1, b=self.b1)
        self.assertEqual(self.levels(d), [ERROR])

    def test_a_side_not_yet_connected_is_a_gap_not_an_error(self):
        d = self.drain(self.x, a=self.b1, b=self.creek)
        self.assertEqual(self.levels(d), [UNVERIFIABLE])
        self.assertEqual(self.chains(d)[1], (None, ['Creek', 'Frag'], 'gap'))

    def test_a_missing_mouth_altitude_is_unverifiable(self):
        self.y.mouth.altitude = None
        River.objects.filter(pk=self.y.pk).update(mouth=None)
        d = self.drain(self.x, a=self.b1, b=self.y)
        self.assertEqual(self.levels(d), [UNVERIFIABLE])

    def test_rivers_sharing_the_junction_are_fine(self):
        River.objects.filter(pk=self.z.pk).update(mouth=self.x.mouth)
        d = self.drain(self.x, a=self.b1, b=self.z)
        self.assertEqual(d.findings, [])


class WithoutConfluenceTests(DrainageTestCase):
    def test_the_same_river_twice_is_a_spur_meeting_on_it(self):
        d = self.drain(a=self.x, b=self.x)
        self.assertEqual((d.meet, d.meet_river), ('on', self.x.pk))
        self.assertEqual(d.findings, [])

    def test_one_side_refined_the_other_on_the_river_meet_on_it(self):
        d = self.drain(a=self.b1, b=self.x)
        self.assertEqual((d.meet, d.meet_river), ('on', self.x.pk))
        self.assertEqual(self.chains(d), [(None, ['B1', 'X'], 'target'), (None, ['X'], 'target')])
        self.assertEqual(d.findings, [])

    def test_two_catalogued_mouths_name_the_confluence_as_a_hint(self):
        d = self.drain(a=self.b1, b=self.y)
        self.assertEqual((d.meet, d.meet_river), ('on', self.p.pk))
        self.assertEqual(self.levels(d), [HINT])
        self.assertIn('mouth of X into P', d.findings[0].message)

    def test_sides_not_connected_to_each_other(self):
        d = self.drain(a=self.b1, b=self.creek)
        self.assertEqual(d.meet, 'unknown')
        self.assertEqual(self.levels(d), [UNVERIFIABLE])

    def test_one_side_alone_runs_to_the_sea(self):
        d = self.drain(a=self.b1)
        self.assertEqual(self.chains(d), [(None, ['B1', 'X', 'P', 'Sea', 'Ocean'], 'sea')])
        self.assertIsNone(d.meet)


class CleanTests(DrainageTestCase):
    def test_a_contradiction_is_refused_on_its_field(self):
        col = make_col(None, 1200.0, 49.0, 20.0)
        col.confluence_river, col.river_a, col.river_b = self.x, self.b1, self.z
        with self.assertRaises(ValidationError) as caught:
            col.clean()
        self.assertIn('river_b', caught.exception.message_dict)

    def test_a_gap_is_accepted(self):
        col = make_col(None, 1200.0, 49.0, 20.0)
        col.confluence_river, col.river_a, col.river_b = self.x, self.b1, self.creek
        col.clean()


class PageTests(DrainageTestCase):
    def page(self, **fields):
        col = make_col(None, 1200.0, 49.0, 20.0)
        Col.objects.filter(pk=col.pk).update(**fields)
        return self.client.get(col.get_absolute_url()).content.decode()

    def section(self, html):
        return html.split('<div class="section drainage">')[1].split('<h3>')[0]

    def test_two_columns_and_the_meeting_point(self):
        html = self.section(self.page(confluence_river=self.x, river_a=self.y, river_b=self.b1))
        tributary = html.split('<div class="drainage-side left">')[1].split('</div>')[0]
        main = html.split('<div class="drainage-side right">')[1].split('</div>')[0]
        self.assertIn('tributary side', tributary)
        self.assertIn('>B1', tributary)
        self.assertIn('main side', main)
        self.assertIn('>Y', main)
        # The meeting rivers are not repeated in the columns but form the junction row:
        # the confluence river under the tributary side, an arrow, its parent under the main.
        self.assertNotIn('>X', tributary)
        self.assertNotIn('>P<', main)
        left = html.index('<div class="meet left">')
        arrow = html.index('<div class="meet arrow"')
        right = html.index('<div class="meet right">')
        self.assertIn('>X', html[left:arrow])
        self.assertIn('>P', html[right:])
        self.assertIn('500.0', html[arrow:right])          # the junction's altitude
        self.assertNotIn('The waters meet', html)
        self.assertNotIn('class="error"', html)

    def test_only_a_confluence_is_just_the_junction_row(self):
        html = self.section(self.page(confluence_river=self.x))
        self.assertNotIn('drainage-side', html)
        self.assertIn('<div class="meet arrow"', html)

    def test_a_gap_is_shown_as_such(self):
        html = self.section(self.page(confluence_river=self.x, river_a=self.b1, river_b=self.creek))
        self.assertIn('not yet connected', html)
        self.assertIn('class="unverifiable"', html)

    def test_nothing_recorded(self):
        self.assertIn('No drainage recorded', self.section(self.page()))

    def test_the_drainage_costs_a_fixed_four_queries(self):
        # The river tree, the side rivers shown, and their junctions for the map (two).
        col = make_col(None, 1200.0, 49.0, 20.0)
        Col.objects.filter(pk=col.pk).update(confluence_river=self.x, river_a=self.y,
                                             river_b=self.b1)
        url = col.get_absolute_url()
        with self.assertNumQueries(self.queries_without_drainage(url) + 4):
            self.client.get(url)

    def queries_without_drainage(self, url):
        from unittest import mock
        from mountains.views.col import col as views
        empty = {'sides': [], 'meet': None, 'meet_river': None, 'meet_into': None,
                 'findings': [], 'runs': []}
        with mock.patch.object(views, 'drainage_context', return_value=empty):
            from django.db import connection
            from django.test.utils import CaptureQueriesContext
            with CaptureQueriesContext(connection) as queries:
                self.client.get(url)
        return len(queries)


class RunTests(DrainageTestCase):
    """The stretch of each river a drop runs along, for the col's map."""

    def runs(self, confluence=None, a=None, b=None):
        from mountains.views.col.col import drainage_runs
        drainage = self.drain(confluence, a, b)
        rivers = River.objects.select_related('source', 'mouth').in_bulk()
        return [(river.source.name,
                 enter and rivers_by_mouth(enter), leave and rivers_by_mouth(leave))
                for river, enter, leave in drainage_runs(drainage, rivers)]

    def test_each_river_from_where_the_one_above_joins_to_where_it_leaves(self):
        self.assertEqual(self.runs(self.x, a=self.y, b=self.b1), [
            ('B1', None, 'B1'), ('X', 'B1', 'X'),       # the tributary side, down to the junction
            ('Y', None, 'Y'), ('P', 'Y', 'X'),          # the main side leaves P at X's mouth
        ])

    def test_a_side_that_is_the_parent_itself_has_no_stretch_to_draw(self):
        self.assertEqual(self.runs(self.x, a=self.b1, b=self.p), [('B1', None, 'B1'), ('X', 'B1', 'X')])

    def test_on_a_common_river_it_runs_between_the_two_entries(self):
        self.assertEqual(self.runs(a=self.b1, b=self.y),
                         [('B1', None, 'B1'), ('X', 'B1', 'X'), ('Y', None, 'Y'), ('P', 'Y', 'X')])

    def test_the_course_is_cut_between_the_points_given(self):
        p = River.objects.get(pk=self.p.pk)
        names = [point.name for point in p.course(self.y.mouth_id, self.x.mouth_id)]
        self.assertEqual(names, ['Y mouth', 'X mouth'])          # Z joins lower: not on it
        self.assertEqual([point.name for point in p.course()],
                         ['P', 'Y mouth', 'X mouth', 'Z mouth', 'P mouth'])


def rivers_by_mouth(point_pk):
    """ Which river a mouth point belongs to, by name, for readable expectations. """
    return River.objects.get(mouth_id=point_pk).source.name


class DownMarkTests(DrainageTestCase):
    def test_the_down_variant_of_each_bank_and_of_an_unrecorded_one(self):
        for side, code in (('L', 'left'), ('R', 'right'), ('O', 'undecided'), (None, 'unrecorded')):
            with self.subTest(side=side):
                self.b1.mouth_side = side
                self.assertIn(f'href="#mouth-side-{code}-down"', self.b1.mouth_side_abbr_down())

    def test_every_down_variant_is_in_the_sprite(self):
        from django.template.loader import render_to_string
        sprite = render_to_string('mountains/blocks/mouth-side-sprite.html')
        for code in ('left', 'right', 'undecided', 'sea', 'unrecorded'):
            self.assertIn(f'id="mouth-side-{code}-down"', sprite)

    def test_each_step_in_a_column_carries_the_mark_of_the_river_joining_it(self):
        River.objects.filter(pk=self.b1.pk).update(mouth_side='L')
        col = make_col(None, 1200.0, 49.0, 20.0)
        Col.objects.filter(pk=col.pk).update(river_a=self.b1, river_b=self.creek)
        html = self.client.get(col.get_absolute_url()).content.decode()
        # B1 joins X from the left bank; X is shown with that mark, flowing down.
        self.assertRegex(html, r'#mouth-side-left-down"></use></svg></abbr>\s*<a [^>]*>X<')
