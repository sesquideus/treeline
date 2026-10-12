"""
Clicking a feature on the map.

A click on a summit, col or river opens its detail page in a new tab. `detailUrl` decides
which page — it is pure, so it runs here for real (see `test_js_runtime`); the click handler
itself needs OpenLayers and a DOM and stays source-asserted.

The part worth pinning is what happens to everything that is *not* one of those three. The
map carries plenty of marks with no page of their own — isolation points, lineage segments,
highlight rings, range polygons — and a click on one of those has to fall through to the
popup rather than navigating somewhere wrong or nowhere.
"""

from django.test import SimpleTestCase

from mountains.test_js_runtime import evaluate, feature_expression

MAP_JS = 'static/js/map.js'


def detail_url(**properties):
    return evaluate('detailUrl(%s)' % feature_expression(properties))


class DetailUrlTests(SimpleTestCase):
    def test_the_three_kinds_with_a_page_reach_it(self):
        self.assertEqual(detail_url(type='summit', pk=27), '/summit/27/')
        self.assertEqual(detail_url(type='col', pk=82), '/col/82/')
        self.assertEqual(detail_url(type='river', pk=7), '/river/7/')

    def test_the_urls_match_the_ones_the_popup_links_to(self):
        """One source of truth for each; a second spelling would drift."""
        self.assertEqual(detail_url(type='summit', pk=27), evaluate('summitUrl(27)'))
        self.assertEqual(detail_url(type='col', pk=82), evaluate('colUrl(82)'))
        self.assertEqual(detail_url(type='river', pk=7), evaluate('riverUrl(7)'))

    def test_marks_with_no_page_of_their_own_have_no_url(self):
        for kind in ('isolation_point', 'isolation_parent', 'prominence_parent',
                     'encirclement_parent', 'isolation_circle', 'range',
                     'highlight_summit', 'highlight_parent', 'confluence_col'):
            with self.subTest(type=kind):
                self.assertIsNone(detail_url(type=kind, pk=5))

    def test_a_feature_with_no_pk_has_no_url(self):
        """Rather than `/summit/undefined/`, which would 404 on a page that exists."""
        self.assertIsNone(detail_url(type='summit'))

    def test_no_feature_at_all_has_no_url(self):
        self.assertIsNone(evaluate('detailUrl(null)'))


class ClickWiringTests(SimpleTestCase):
    """The handler itself: OpenLayers events and `window.open`, both past the harness."""

    def source(self):
        with open(MAP_JS, encoding='utf-8') as handle:
            return '\n'.join(line for line in handle
                             if not line.lstrip().startswith('//'))

    def handler(self):
        source = self.source()
        return source.split("map.on('click'")[1].split('\n    });')[0]

    def test_it_opens_a_new_tab_without_handing_over_this_one(self):
        self.assertIn("window.open(url, '_blank', 'noopener')", self.handler())

    def test_it_listens_on_click_not_singleclick(self):
        """
        `singleclick` waits 250 ms to rule out a double click, and by then the browser no
        longer counts the `window.open` as user-initiated — the popup blocker eats it.
        """
        source = self.source()
        self.assertIn("map.on('click'", source)
        self.assertNotIn('singleclick', source)

    def test_a_feature_with_no_page_falls_through_to_the_popup(self):
        handler = self.handler()
        self.assertIn('if (url &&', handler)
        self.assertIn('hovered(feature, e.coordinate)', handler)

    def test_a_device_without_hover_gets_its_popup_first(self):
        """
        Otherwise the popup is unreachable on a phone: the first tap would navigate away
        before anything could be read.
        """
        source = self.source()
        self.assertIn("window.matchMedia('(hover: hover)')", source)
        self.assertIn('canHover || feature === described', source)
