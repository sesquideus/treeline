"""
The river popup, rendered.

These run the real `popupHtml()` out of `static/js/map.js` (see `test_js_runtime`), so what
is asserted is the markup a browser would show rather than the presence of a line in the
source. That matters here because the popup has been through several rounds of rearranging:
rows moving between sections, a mark changing from an arrow to a sprite, summits becoming
links. Every one of those is invisible to a substring search over the file.

The feature properties below are the shape `River.to_dict()` produces — `mountains/
test_river_mouth_side.py` pins that end, this one pins what the popup does with it.

Note that `watershed_high_point` carries *two* ids: `pk` is a NamedPoint and addresses
`/point/<pk>/`, `summit` is the Summit the point belongs to if it is one at all, and exists
only so the hover highlight can find a marker. The fixture deliberately gives them different
values, because the sequences overlap in reality and a test where they matched would pass
however they were confused.
"""

from django.test import SimpleTestCase

from mountains.test_js_runtime import popup_html, rows, text

FULL = dict(
    type='river', pk=7, name='Poprad',
    parent={'id': 3, 'name': 'Dunajec'},
    source={'lat': 49.05, 'lon': 20.4, 'alt': 1500.0},
    mouth={'lat': 48.9, 'lon': 20.6, 'alt': 500.0},
    mouth_side={'code': 'left', 'title': 'joins from the left bank, looking downstream'},
    source_summit={'pk': 42, 'name': 'Kriváň', 'alt': 2494.0},
    watershed_high_point={'pk': 6120, 'summit': 1,
                          'name': 'Gerlachovský štít', 'alt': 2654.4},
)


def river(**overrides):
    return popup_html(**{**FULL, **overrides})


class RiverPopupLayoutTests(SimpleTestCase):
    """No database: this is all string in, markup out."""

    def sections(self, html):
        return [section for section, _, _ in rows(html)]

    def test_the_caption_links_to_the_river(self):
        self.assertIn('<a href="/river/7/">Poprad</a>', river())

    def test_the_sections_run_source_then_mouth_then_watershed(self):
        self.assertEqual(list(dict.fromkeys(self.sections(river()))),
                         ['source', 'mouth', 'watershed HP'])

    def test_every_row_lands_in_its_own_section(self):
        self.assertEqual([(s, l) for s, l, _ in rows(river())], [
            ('source', 'altitude'), ('source', 'position'), ('source', 'below'),
            ('mouth', 'flows into'), ('mouth', 'altitude'), ('mouth', 'position'),
            ('watershed HP', ''),
        ])


class RiverPopupSummitTests(SimpleTestCase):
    def row(self, html, section):
        return next(value for s, _, value in rows(html) if s == section)

    def test_the_watershed_high_point_links_through_the_point_not_the_summit(self):
        """
        `/point/6120/`, not `/summit/1/`. Both numbers are in the payload and both address a
        real row, so the wrong one produces a working link to an unrelated mountain — which
        is exactly why `pointLabel` and `summitLabel` are separate functions.
        """
        value = self.row(river(), 'watershed HP')
        self.assertIn('<a class="mountain" href="/point/6120/">Gerlachovský štít</a>', value)
        self.assertNotIn('/summit/1/', value)
        self.assertEqual(text(value), 'Gerlachovský štít 2654.4 m')

    def test_a_high_point_that_is_no_summit_still_renders_and_still_links(self):
        """The case the retarget exists for: no summit pk at all, and the row is unharmed."""
        html = river(watershed_high_point={'pk': 4471, 'summit': None,
                                           'name': 'Bystrá sedlovka', 'alt': 1842.0})
        value = self.row(html, 'watershed HP')
        self.assertIn('href="/point/4471/"', value)
        self.assertEqual(text(value), 'Bystrá sedlovka 1842.0 m')

    def test_a_nameless_high_point_shows_its_description(self):
        html = river(watershed_high_point={'pk': 4472, 'summit': None,
                                           'name': '(unnamed 49.00000° N 19.80000° E)',
                                           'alt': 1300.0})
        self.assertIn('unnamed', text(self.row(html, 'watershed HP')))

    def test_the_peak_above_the_source_is_its_own_row_and_its_own_summit(self):
        below = next(v for s, l, v in rows(river()) if (s, l) == ('source', 'below'))
        self.assertIn('href="/summit/42/">Kriváň</a>', below)
        self.assertNotIn('Gerlach', below)

    def test_a_river_naming_no_high_point_has_no_watershed_section(self):
        """The section drops itself rather than rendering a caption beside nothing."""
        html = river(watershed_high_point=None)
        self.assertNotIn('watershed HP', html)
        self.assertIn('mouth', html)

    def test_a_river_naming_no_source_summit_keeps_the_rest_of_its_source(self):
        html = river(source_summit=None)
        self.assertEqual([(s, l) for s, l, _ in rows(html) if s == 'source'],
                         [('source', 'altitude'), ('source', 'position')])


class RiverPopupMouthSideTests(SimpleTestCase):
    def flows_into(self, html):
        return next(v for s, l, v in rows(html) if (s, l) == ('mouth', 'flows into'))

    def test_the_mark_precedes_the_parent_and_references_the_sprite(self):
        value = self.flows_into(river())
        self.assertLess(value.index('mouth-side-left'), value.index('Dunajec'))
        self.assertIn('<use href="#mouth-side-left">', value)

    def test_the_mark_carries_the_wording_as_a_tooltip(self):
        self.assertIn('<abbr title="joins from the left bank, looking downstream">',
                      self.flows_into(river()))

    def test_each_side_reaches_its_own_symbol(self):
        for code in ('left', 'right', 'undecided', 'sea'):
            with self.subTest(code=code):
                html = river(mouth_side={'code': code, 'title': code})
                self.assertIn(f'#mouth-side-{code}', self.flows_into(html))

    def test_an_unrecorded_side_leaves_the_parent_unmarked(self):
        value = self.flows_into(river(mouth_side=None))
        self.assertNotIn('mouth-side', value)
        self.assertIn('Dunajec', value)

    def test_a_river_with_no_parent_says_so_rather_than_vanishing(self):
        self.assertEqual(text(self.flows_into(river(parent=None))), '—')


class RiverPopupEmptyTests(SimpleTestCase):
    def test_a_river_with_nothing_recorded_still_names_itself(self):
        html = popup_html(type='river', pk=8, name='Nameless', parent=None, source=None,
                          mouth=None, mouth_side=None, source_summit=None,
                          watershed_high_point=None)
        self.assertIn('<a href="/river/8/">Nameless</a>', html)
        # Only the mouth's first row survives, and it is the em-dash.
        self.assertEqual([(s, l) for s, l, _ in rows(html)], [('mouth', 'flows into')])

    def test_an_unnamed_river_falls_back_rather_than_printing_undefined(self):
        html = popup_html(type='river', pk=9, parent=None, source=None, mouth=None,
                          mouth_side=None, source_summit=None,
                          watershed_high_point=None)
        self.assertIn('unknown', html)
        self.assertNotIn('undefined', html)
