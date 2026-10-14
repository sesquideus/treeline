"""
Comparing two peaks: the summit they both descend from, and the chains that get there.

The page had been blank for months — its content lived in `{% block page_content %}`, which
`core/map-page.html` does not have, and Django discards a block the parent never renders —
so none of this was observable from outside. These tests hold both halves: the walk itself,
which is ordinary Python and can be checked exactly, and the GeoJSON the map draws, where
the thing most worth pinning is that every feature type served has a style to be drawn with.

The fixture is a small forest, built so that every case has a distinct answer:

        Root 3000 ── key col 100 m, far below everything and never the separating col
        ├── Left 2500 ── col 2200
        │   └── Peak One 2100 ── col 1900
        └── Right 2400 ── col 2000
            └── Peak Two 2050 ── col 1800   ← the lowest on the way between the peaks

    Island 1200, a root of its own, sharing nothing with any of them.
"""

import re

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from mountains.models import Summit
from mountains.test_factories import make_col, make_summit
from mountains.test_js_runtime import evaluate
from mountains.views.summit.json import build_comparison_features
from mountains.views.summit.summit import prominence_chain, separation

STYLES_JS = 'static/js/styles.js'


def build_forest():
    """The tree above, returned by name."""
    peaks = {}
    for name, altitude, parent, col_altitude, lat, lon in [
        ('Root', 3000, None, 100, 49.00, 20.00),
        ('Left', 2500, 'Root', 2200, 49.10, 20.10),
        ('Right', 2400, 'Root', 2000, 49.20, 20.20),
        ('Peak One', 2100, 'Left', 1900, 49.30, 20.30),
        ('Peak Two', 2050, 'Right', 1800, 49.40, 20.40),
        ('Island', 1200, None, None, -20.00, 150.00),
    ]:
        col = (make_col(f'{name} col', col_altitude, lat - 0.01, lon - 0.01)
               if col_altitude is not None else None)
        peaks[name] = make_summit(name, altitude, lat, lon, key_col=col,
                                  prominence_parent=peaks.get(parent))
    return peaks


class AncestryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_forest()

    def ancestry(self, one, other):
        ancestor, mine, theirs = self.peaks[one].common_prominence_ancestry(self.peaks[other])
        return (ancestor and ancestor.point.name,
                [summit.point.name for summit in mine],
                [summit.point.name for summit in theirs])

    def test_two_peaks_meet_at_the_summit_above_both(self):
        self.assertEqual(self.ancestry('Peak One', 'Peak Two'),
                         ('Root', ['Peak One', 'Left'], ['Peak Two', 'Right']))

    def test_the_chains_stop_below_the_ancestor_so_it_is_named_once(self):
        """`mine + [ancestor] + reversed(theirs)` is the whole walk, with nothing repeated."""
        ancestor, mine, theirs = self.ancestry('Peak One', 'Peak Two')
        self.assertNotIn(ancestor, mine)
        self.assertNotIn(ancestor, theirs)

    def test_a_peak_below_another_meets_it_with_an_empty_chain(self):
        self.assertEqual(self.ancestry('Peak One', 'Left'), ('Left', ['Peak One'], []))

    def test_a_peak_compared_with_itself_is_its_own_ancestor(self):
        self.assertEqual(self.ancestry('Peak One', 'Peak One'), ('Peak One', [], []))

    def test_peaks_in_different_trees_have_no_ancestor_at_all(self):
        """A forest, not a tree: nothing joins one landmass's root to another's."""
        self.assertEqual(self.ancestry('Peak One', 'Island'), (None, [], []))

    def test_a_parent_cycle_stops_the_walk_rather_than_spinning(self):
        """
        `clean()` refuses to build one, but `save()` does not validate, so a cycle can
        reach the database. Walking it without a guard never returns.
        """
        one, two = self.peaks['Peak One'], self.peaks['Peak Two']
        Summit.objects.filter(pk=one.pk).update(prominence_parent=two)
        Summit.objects.filter(pk=two.pk).update(prominence_parent=one)
        walked = [summit.pk for summit in Summit.objects.get(pk=one.pk).prominence_walk()]
        self.assertEqual(walked, [one.pk, two.pk])


class SeparationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_forest()

    def test_it_names_the_lowest_col_on_the_way_between_the_peaks(self):
        ancestor, col = separation(self.peaks['Peak One'], self.peaks['Peak Two'])
        self.assertEqual(ancestor.point.name, 'Root')
        self.assertEqual(col.point.altitude, 1800)

    def test_the_ancestors_own_col_is_not_a_candidate(self):
        """
        It sits below everything here on purpose. That col belongs to the ground *above*
        the fork; crossing it is not part of getting from one peak to the other.
        """
        _, col = separation(self.peaks['Peak One'], self.peaks['Peak Two'])
        self.assertNotEqual(col.point.altitude, 100)

    def test_peaks_in_different_trees_separate_at_nothing(self):
        self.assertIsNone(separation(self.peaks['Peak One'], self.peaks['Island']))

    def test_an_ancestor_with_no_cols_on_the_way_is_still_reported(self):
        """
        The two answers are independent: a chain nobody has given key cols yet still joins
        the peaks, and the page should say so rather than claim they are unrelated.
        """
        Summit.objects.filter(point__name__in=['Peak One', 'Left']).update(key_col=None)
        ancestor, col = separation(Summit.objects.get(point__name='Peak One'),
                                   self.peaks['Left'])
        self.assertEqual(ancestor.point.name, 'Left')
        self.assertIsNone(col)


class ChainTests(TestCase):
    """
    The chain as the table reads it: peak, key col, parent, key col, … over the summit the
    two share and down the far side. The interleaving is the whole point — a col row that
    drifts one position names the saddle of the wrong link — so these assert the sequence
    rather than its contents.
    """

    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_forest()

    def chain(self, one='Peak One', other='Peak Two'):
        return prominence_chain(self.peaks[one], self.peaks[other])

    def named(self, chain):
        return [(step.kind, step.obj and step.obj.point.name) for step in chain]

    def test_it_runs_peak_col_parent_col_over_the_top_and_back_down(self):
        self.assertEqual(self.named(self.chain()), [
            ('peak', 'Peak One'), ('col', 'Peak One col'),
            ('summit', 'Left'), ('col', 'Left col'),
            ('ancestor', 'Root'),
            ('col', 'Right col'), ('summit', 'Right'),
            ('col', 'Peak Two col'), ('peak', 'Peak Two'),
        ])

    def test_each_col_sits_on_the_link_it_is_the_key_col_of(self):
        """
        Descending, the summit comes *after* its own col, which is the one place the order
        could plausibly be written the other way round and still look like a chain.
        """
        chain = self.chain()
        for index, step in enumerate(chain):
            if step.kind != 'col':
                continue
            # Sliced rather than indexed: a col pushed to either end of the chain is one of
            # the ways the order can go wrong, and should fail here rather than raise.
            neighbours = chain[max(index - 1, 0):index] + chain[index + 1:index + 2]
            owners = [side.obj for side in neighbours
                      if side.obj and side.obj.key_col_id == step.obj.pk]
            self.assertEqual(len(owners), 1,
                             f'{step.obj.point.name} is not the key col of either neighbour')

    def test_the_ancestor_appears_once_and_only_once(self):
        kinds = [step.kind for step in self.chain()]
        self.assertEqual(kinds.count('ancestor'), 1)

    def test_it_climbs_to_the_ancestor_and_descends_from_it(self):
        directions = [step.direction for step in self.chain()]
        self.assertEqual(directions, ['up'] * 4 + [''] + ['down'] * 4)

    def test_the_altitudes_rise_to_the_fork_and_fall_after_it(self):
        """A prominence parent is always the higher ground, so the arrows cannot lie."""
        chain = self.chain()
        summits = [step.obj.point.altitude for step in chain if step.kind != 'col']
        top = summits.index(max(summits))
        self.assertEqual(summits[:top + 1], sorted(summits[:top + 1]))
        self.assertEqual(summits[top:], sorted(summits[top:], reverse=True))

    def test_the_deepest_col_on_the_way_is_the_flagged_one(self):
        flagged = [step.obj.point.altitude for step in self.chain() if step.lowest]
        self.assertEqual(flagged, [1800])

    def test_the_flagged_col_is_the_one_the_prose_names(self):
        """
        The table and the paragraph above it are one answer rendered twice; they read the
        same list so that they cannot drift apart.
        """
        _, col = separation(self.peaks['Peak One'], self.peaks['Peak Two'])
        self.assertEqual([step.obj for step in self.chain() if step.lowest], [col])

    def test_a_link_whose_col_is_unrecorded_keeps_its_row(self):
        """
        Dropping it would shorten the chain and silently rename every col below: the reader
        would see one fewer link than there is. The rung stays, empty.
        """
        Summit.objects.filter(point__name='Left').update(key_col=None)
        chain = prominence_chain(Summit.objects.get(point__name='Peak One'),
                                 self.peaks['Peak Two'])
        self.assertEqual(self.named(chain)[:5], [
            ('peak', 'Peak One'), ('col', 'Peak One col'),
            ('summit', 'Left'), ('col', None),
            ('ancestor', 'Root'),
        ])

    def test_a_peak_below_another_ends_at_it_rather_than_repeating_it(self):
        self.assertEqual(self.named(self.chain('Peak One', 'Left')),
                         [('peak', 'Peak One'), ('col', 'Peak One col'),
                          ('ancestor', 'Left')])

    def test_a_peak_compared_with_itself_is_a_chain_of_one(self):
        self.assertEqual(self.named(self.chain('Peak One', 'Peak One')),
                         [('ancestor', 'Peak One')])

    def test_peaks_in_different_trees_have_no_chain_to_show(self):
        self.assertEqual(self.chain('Peak One', 'Island'), [])

    def test_every_step_knows_what_to_call_itself(self):
        """
        `role` and `css` are read straight into the template, where a missing key would be
        an empty cell and a wrong one a link painted as the wrong kind of thing.
        """
        for step in self.chain():
            with self.subTest(kind=step.kind):
                self.assertTrue(step.role)
                self.assertEqual(step.css, 'col' if step.kind == 'col' else 'mountain')


class ComparisonFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_forest()

    def features(self, one='Peak One', other='Peak Two'):
        return build_comparison_features(self.peaks[one], self.peaks[other])

    def kinds(self, features):
        return [feature['properties']['type'] for feature in features]

    def named(self, features, kind):
        return [f['properties']['name'] for f in features if f['properties']['type'] == kind]

    def test_both_peaks_their_chains_and_the_meeting_point_are_all_drawn(self):
        kinds = self.kinds(self.features())
        self.assertEqual(kinds.count('summit'), 2)          # the two compared
        self.assertEqual(kinds.count('common_ancestor'), 1)  # Root
        self.assertEqual(kinds.count('prominence_parent'), 2)  # Left and Right
        self.assertEqual(kinds.count('prominence_line'), 4)   # two links per chain

    def test_each_line_runs_through_the_col_between_its_two_summits(self):
        """Three points, not two — the middle one is what the routing checkbox drops."""
        lines = [f for f in self.features() if f['properties']['type'] == 'prominence_line']
        for line in lines:
            with self.subTest(line=line['geometry']['coordinates']):
                self.assertEqual(len(line['geometry']['coordinates']), 3)

    def test_the_separating_col_is_the_one_called_out(self):
        called_out = [name for name in self.named(self.features(), 'col')
                      if 'lowest connecting col' in name]
        self.assertEqual(len(called_out), 1)
        self.assertIn('Peak Two col', called_out[0])

    def test_every_mark_links_to_the_page_it_stands_for(self):
        for feature in self.features():
            if feature['geometry']['type'] == 'Point':
                with self.subTest(name=feature['properties']['name']):
                    self.assertTrue(feature['properties']['url'].startswith('/'))

    def test_a_peak_that_is_the_other_s_ancestor_keeps_its_own_mark(self):
        """Otherwise it would be drawn twice, once as a peak and once as the meeting point."""
        kinds = self.kinds(self.features('Peak One', 'Left'))
        self.assertEqual(kinds.count('summit'), 2)
        self.assertNotIn('common_ancestor', kinds)

    def test_peaks_in_different_trees_are_drawn_with_nothing_between_them(self):
        kinds = self.kinds(self.features('Peak One', 'Island'))
        self.assertEqual(kinds, ['summit', 'summit'])

    def test_every_type_served_has_a_style_to_be_drawn_with(self):
        """
        The failure this is for leaves no trace: `styleFor()` returns `[]` for a type it
        does not know, so the feature is simply not rendered. The detail map's prominence
        line was invisible that way for months.
        """
        with open(STYLES_JS, encoding='utf-8') as handle:
            styles = handle.read()
        for kind in sorted(set(self.kinds(self.features()))):
            with self.subTest(type=kind):
                self.assertIn(f"case '{kind}':", styles)


class ComparisonPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_forest()

    def page(self, one='Peak One', other='Peak Two', by_path=False):
        one, other = self.peaks[one], self.peaks[other]
        url = (reverse('summit-comparison-geojson', args=[one.pk, other.pk]).rsplit('/', 1)[0]
               + '/' if by_path else reverse('summit-compare'))
        response = (self.client.get(url) if by_path
                    else self.client.get(url, {'summit1': one.pk, 'summit2': other.pk}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_common_ancestor_is_named_and_linked(self):
        html = self.page()
        root = self.peaks['Root']
        self.assertIn(root.get_absolute_url(), html)
        self.assertRegex(html, re.compile(r'lowest summit standing above both.*Root', re.S))

    def test_the_path_form_of_the_url_shows_the_same_pair(self):
        """`/summit/compare/<pk1>/<pk2>/` is a route; it used to render an empty form."""
        self.assertEqual(re.sub(r'\s+', ' ', self.page(by_path=True)),
                         re.sub(r'\s+', ' ', self.page()))

    def test_the_map_is_pointed_at_the_pair_s_own_collection(self):
        one, two = self.peaks['Peak One'], self.peaks['Peak Two']
        self.assertIn(reverse('summit-comparison-geojson', args=[one.pk, two.pk]),
                      self.page())

    def test_peaks_with_no_common_ancestor_say_so_rather_than_breaking(self):
        html = self.page('Peak One', 'Island')
        self.assertIn('no common ancestor', html)

    def test_the_chain_is_shown_as_a_table_in_the_order_it_is_walked(self):
        """
        One row per rung, interleaved. The names are asserted in sequence rather than by
        presence: the peaks and cols all appear either way, and the order is the content.
        """
        rows = re.findall(r'<tr class="rung-(peak|summit|ancestor|col)[^"]*">(.*?)</tr>',
                          re.sub(r'\s+', ' ', self.page()), re.S)
        named = [(kind, re.search(r'>([^<>]*?)</a>', row).group(1).strip()
                  if '</a>' in row else None) for kind, row in rows]
        self.assertEqual(named, [
            ('peak', 'Peak One'), ('col', 'Peak One col'),
            ('summit', 'Left'), ('col', 'Left col'),
            ('ancestor', 'Root'),
            ('col', 'Right col'), ('summit', 'Right'),
            ('col', 'Peak Two col'), ('peak', 'Peak Two'),
        ])

    def test_the_two_rows_the_prose_names_are_marked_for_the_eye(self):
        """
        On a real chain this table runs past twenty rows, and the fork and the deepest col
        are the two the paragraphs above it talk about. Both carry a class to be tinted by.
        """
        html = re.sub(r'\s+', ' ', self.page())
        self.assertRegex(html, r'<tr class="rung-ancestor"[^>]*>(?:(?!</tr>).)*Root')
        self.assertRegex(html, r'<tr class="rung-col lowest"[^>]*>(?:(?!</tr>).)*Peak Two col')

    def test_the_map_scripts_are_all_there(self):
        """
        `initSummitMap` calls `styleFor`, which is in `styles.js`; without it the map died
        in a rejected promise while the page still returned 200. Asserted on the rendered
        page as well as in `test_summit_detail_map.ScriptTests`, because this is the page
        it actually happened to.
        """
        html = self.page()
        for script in ('js/map.js', 'js/styles.js', 'js/summit-detail-map.js'):
            self.assertIn(script, html)

    def test_a_pair_with_no_chain_shows_no_table(self):
        self.assertNotIn('The way between them', self.page('Peak One', 'Island'))

    def test_the_empty_page_still_offers_the_form(self):
        response = self.client.get(reverse('summit-compare'))
        self.assertContains(response, 'Compare mountains')
        self.assertContains(response, 'name="summit1"')


class ComparisonEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_forest()

    def test_it_serves_a_feature_collection(self):
        one, two = self.peaks['Peak One'], self.peaks['Peak Two']
        payload = self.client.get(
            reverse('summit-comparison-geojson', args=[one.pk, two.pk])).json()
        self.assertEqual(payload['type'], 'FeatureCollection')
        # two peaks, the summit above them, two summits between, four cols, four lines
        self.assertEqual(len(payload['features']), 13)

    def test_an_unknown_summit_is_a_404_rather_than_an_empty_map(self):
        one = self.peaks['Peak One']
        response = self.client.get(
            reverse('summit-comparison-geojson', args=[one.pk, 10 ** 6]))
        self.assertEqual(response.status_code, 404)


def style_count(kind):
    """
    How many styles `styleFor()` returns for a feature of this type, run for real.

    Zero is the failure mode worth catching: an unknown type falls to `default: return []`
    and the feature is drawn as nothing at all, with no error anywhere.
    """
    return evaluate("""styleFor({
        get: key => key === 'type' ? %s : null,
        getGeometry: () => ({ getCoordinates: () => [[0, 0], [1, 1], [2, 2]] }),
    }).length""" % repr(kind).replace("'", '"'), source=STYLES_JS)


class StyleTests(SimpleTestCase):
    """`styles.js`, run rather than read — the file's own `case` list proves nothing."""

    def test_the_chain_line_is_drawn(self):
        self.assertGreater(style_count('prominence_line'), 0)

    def test_the_meeting_point_is_drawn(self):
        self.assertGreater(style_count('common_ancestor'), 0)

    def test_an_unknown_type_is_drawn_as_nothing(self):
        """The control: this is what the two above looked like before they had a case."""
        self.assertEqual(style_count('no_such_type'), 0)


class RoutingSourceTests(SimpleTestCase):
    """
    The comparison map draws one line per link of the chain, where the detail map draws one
    in total. Straightening only the first would be worse than straightening none.
    """

    def test_every_prominence_line_is_routed_and_not_merely_the_first(self):
        with open('static/js/summit-detail-map.js', encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn(".filter(feature => feature.get('type') === 'prominence_line')", source)
        self.assertIn('lines.forEach(line => routeThroughCol(line', source)
