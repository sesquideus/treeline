"""
The summit search boxes on the compare page.

The two pickers were `<select>`s holding every summit in the database — 2410 options each,
315 kB of HTML per render, and nothing to do but scroll. They are now text boxes with
suggestions, in the shape the admin's autocomplete uses.

The box carries the *name*, not a hidden primary key, which is the decision everything else
follows from: a name typed in full and submitted with no JavaScript at all still works, and
there is no hidden value that can drift away from what is on screen. It costs a resolution
step on the way in, and that step is most of what is tested here — including that a pk still
resolves, because `?summit1=58` is a URL people may already have.

What is not tested here is the dropdown itself, which needs a DOM; only the keyboard
movement through it, which does not.
"""

import json
import re

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from mountains.forms.summit import CompareForm, SummitSearchField
from mountains.test_factories import make_summit
from mountains.test_js_runtime import SEARCH_JS, evaluate
from mountains.views.summit.json import SummitSearch


def build_summits():
    """
    Names chosen for the cases that matter: one name that is a prefix of another, two that
    merely contain the query — one of them sorting *before* both prefix matches, so that
    ranking and plain alphabetical order give different answers — and one with no name.
    """
    return {name: make_summit(name, altitude, 49.0 + index / 100, 20.0 + index / 100)
            for index, (name, altitude) in enumerate([
                ('Kriv', 2000),            # a name that is a prefix of the next
                ('Kriván', 2494),
                ('Dolina Krivá', 1500),    # contains it, and sorts first of all of them
                ('Malý Kriván', 1700),     # contains it, and sorts last
                ('Rysy', 2500),
                (None, 900),               # nameless: reachable by pk and nothing else
            ])}


class SearchEndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_summits()

    def search(self, query):
        response = self.client.get(reverse('summit-search'), {'q': query})
        self.assertEqual(response.status_code, 200)
        return [result['name'] for result in response.json()['results']]

    def test_it_finds_what_the_name_contains(self):
        self.assertIn('Malý Kriván', self.search('kriv'))

    def test_the_names_that_start_with_it_come_first(self):
        """
        What a reader typing the first letters of a mountain expects. Alphabetical alone
        would bury `Kriván` under every name that happens to contain the letters.
        """
        self.assertEqual(self.search('kriv'),
                         ['Kriv', 'Kriván', 'Dolina Krivá', 'Malý Kriván'])

    def test_it_does_not_care_about_case(self):
        """Including the accents, which are most of what makes these names hard to type."""
        self.assertEqual(self.search('KRIVÁN'), ['Kriván', 'Malý Kriván'])

    def test_one_letter_is_not_a_search(self):
        """It would match hundreds and mean nothing; the box waits for a second letter."""
        self.assertEqual(self.search('k'), [])

    def test_nothing_matching_is_an_empty_list_rather_than_an_error(self):
        self.assertEqual(self.search('nonesuch'), [])

    def test_a_result_carries_what_the_dropdown_shows(self):
        result = self.client.get(reverse('summit-search'), {'q': 'rysy'}).json()['results'][0]
        self.assertEqual(result, {'pk': self.peaks['Rysy'].pk,
                                  'name': 'Rysy', 'altitude': 2500.0})

    def test_it_answers_with_at_most_a_screenful(self):
        self.assertLessEqual(len(self.search('a')), SummitSearch.LIMIT)


class SearchFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_summits()

    def clean(self, value):
        return SummitSearchField().clean(value)

    def test_a_name_resolves_to_its_summit(self):
        self.assertEqual(self.clean('Rysy'), self.peaks['Rysy'])

    def test_a_name_that_is_a_prefix_of_another_resolves_to_itself(self):
        """
        `Kriv` is a whole name and also the start of two others. An exact match is taken
        before a partial one, or the shorter name would be unreachable by typing it.
        """
        self.assertEqual(self.clean('Kriv'), self.peaks['Kriv'])

    def test_part_of_a_name_resolves_when_only_one_summit_has_it(self):
        self.assertEqual(self.clean('Malý'), self.peaks['Malý Kriván'])

    def test_case_and_surrounding_space_do_not_matter(self):
        self.assertEqual(self.clean('  rYsY '), self.peaks['Rysy'])

    def test_a_primary_key_still_resolves(self):
        """`?summit1=58` is a URL people may already have, and the path form carries pks."""
        self.assertEqual(self.clean(str(self.peaks['Rysy'].pk)), self.peaks['Rysy'])

    def test_a_nameless_summit_is_reachable_by_its_key(self):
        """It has nothing to type, so the pk is the only way in."""
        self.assertEqual(self.clean(str(self.peaks[None].pk)), self.peaks[None])

    def test_something_matching_nothing_says_so(self):
        with self.assertRaisesMessage(Exception, 'No summit is called'):
            self.clean('nonesuch')

    def test_something_matching_several_says_so_rather_than_choosing(self):
        """`riv` is inside all three names and is none of them, so there is nothing to pick."""
        with self.assertRaisesMessage(Exception, 'More than one summit matches'):
            self.clean('riv')

    def test_the_box_opens_on_a_name_even_when_handed_a_key(self):
        """
        The view's initial values are pks, and a box showing `91` would be unreadable and,
        worse, would resubmit as something the reader never typed.
        """
        html = CompareForm(initial={'summit1': self.peaks['Rysy'].pk}).as_p()
        self.assertIn('value="Rysy"', html)

    def test_the_box_says_where_its_suggestions_come_from(self):
        self.assertIn(f'data-search-url="{reverse("summit-search")}"', CompareForm().as_p())

    def test_there_is_no_dropdown_of_everything_any_more(self):
        self.assertNotIn('<option', CompareForm().as_p())


class ComparePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.peaks = build_summits()

    def compare(self, **params):
        return self.client.get(reverse('summit-compare'), params).content.decode()

    def test_two_names_compare(self):
        self.assertIn('Comparison of', self.compare(summit1='Kriván', summit2='Rysy'))

    def test_two_keys_still_compare(self):
        html = self.compare(summit1=str(self.peaks['Kriván'].pk),
                            summit2=str(self.peaks['Rysy'].pk))
        self.assertIn('Comparison of', html)

    def test_a_name_matching_nothing_is_said_on_the_page_rather_than_raised(self):
        """
        The view used to read the query string as primary keys and hand the text straight
        to `get_object_or_404`, which is a 500 the moment a box holds a name.
        """
        response = self.client.get(reverse('summit-compare'),
                                   {'summit1': 'nonesuch', 'summit2': 'Rysy'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No summit is called')

    def test_an_empty_box_is_asked_for_rather_than_ignored(self):
        self.assertIn('required', self.compare(summit1='', summit2='Rysy'))

    def test_the_path_form_fills_the_boxes_with_names(self):
        one, two = self.peaks['Kriván'], self.peaks['Rysy']
        html = self.client.get(reverse('summit-compare', args=[one.pk, two.pk])).content.decode()
        self.assertEqual(re.findall(r'name="summit[12]"[^>]*value="([^"]*)"', html),
                         ['Kriván', 'Rysy'])

    def test_the_page_no_longer_carries_the_whole_database(self):
        self.assertNotIn('<option', self.compare())


class KeyboardTests(SimpleTestCase):
    """
    Walking the suggestions with the arrow keys. `moveHighlight` is the one piece of
    `summit-search.js` that is logic rather than DOM plumbing, and the one that is wrong in
    an invisible way: an off-by-one at either end moves the highlight past the list and
    chooses nothing on Enter.
    """

    def highlighted(self, length, *steps):
        """Which item is lit after these moves, starting from nothing highlighted."""
        return evaluate('''(function () {
            const children = [];
            for (let i = 0; i < %d; i++) {
                const classes = {};
                children.push({classList: {
                    contains: name => classes[name] === true,
                    add: name => { classes[name] = true; },
                    remove: name => { classes[name] = false; },
                }});
            }
            const list = {children: children};
            %s
            return children.findIndex(item => item.classList.contains('highlighted'));
        })()''' % (length, '\n'.join(f'moveHighlight(list, {step});' for step in steps)),
                        source=SEARCH_JS)

    def test_the_first_move_down_lights_the_first_item(self):
        self.assertEqual(self.highlighted(3, 1), 0)

    def test_moving_down_walks_the_list(self):
        self.assertEqual(self.highlighted(3, 1, 1, 1), 2)

    def test_it_stops_at_the_bottom_rather_than_falling_off(self):
        self.assertEqual(self.highlighted(3, 1, 1, 1, 1, 1), 2)

    def test_it_stops_at_the_top_too(self):
        self.assertEqual(self.highlighted(3, 1, 1, -1, -1, -1), 0)

    def test_only_one_is_ever_lit(self):
        lit = evaluate('''(function () {
            const children = [];
            for (let i = 0; i < 4; i++) {
                const classes = {};
                children.push({classList: {
                    contains: name => classes[name] === true,
                    add: name => { classes[name] = true; },
                    remove: name => { classes[name] = false; },
                }});
            }
            const list = {children: children};
            moveHighlight(list, 1); moveHighlight(list, 1); moveHighlight(list, 1);
            return children.filter(item => item.classList.contains('highlighted')).length;
        })()''', source=SEARCH_JS)
        self.assertEqual(lit, 1)

    def test_an_empty_list_is_not_walked_at_all(self):
        self.assertIsNone(evaluate('moveHighlight({children: []}, 1)', source=SEARCH_JS))
