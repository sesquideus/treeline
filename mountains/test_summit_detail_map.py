"""
The map on a single summit's page — `static/js/summit-detail-map.js`.

Everything it draws is built server-side by `build_summit_features()` and arrives in one
collection. The only thing the browser decides is whether the prominence line detours
through the key col, so that is what is tested here, together with the two ways this file
had drifted away from the code around it:

* it called `buildLineageLayer()` — `map.js`'s builder for the *global* skeleton, which
  takes `(summits, cols, mode, useRouting)` and reads `summits.features`. It was being
  handed the lineage endpoint's `{summit, ancestors, children}` and two arguments, so
  `mode` was `undefined`, `lineageParentAttr()` fell to its default `null`, and the
  function returned an empty layer and bailed before it could touch anything missing. No
  error anywhere — the line simply stopped being drawn, and the checkbox that was supposed
  to control it had nothing to control.
* it reached through `document.getElementById('toggle-col')` unconditionally, though that
  checkbox is in `core/detail-page.html`, and the comparison page used to extend
  `core/map-page.html`, which has no such thing.
  There it is null, and the TypeError took the whole map down.

Both failed silently in their own way, which is why the tests below run the real functions
rather than reading the file for strings.
"""

import json
import re

from django.test import SimpleTestCase, TestCase

from mountains.test_factories import make_col, make_summit
from mountains.test_template_comments import templates
from mountains.test_js_runtime import evaluate

DETAIL_MAP_JS = 'static/js/summit-detail-map.js'
CALLERS = ('mountains/templates/mountains/summit/detail.html',
           'mountains/templates/mountains/summit/comparison.html')

#: A prominence line as the server draws it: summit, key col, parent. The col is the middle
#: vertex, and nothing else on the page records where it was.
ROUTED = [[20.0, 49.0], [20.05, 48.98], [20.1, 49.1]]


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def arguments(text):
    """
    The comma-separated parts of `text`, ignoring commas inside brackets.

    A call site passes its coordinates as `[{{ lon }}, {{ lat }}]`, which `str.split(',')`
    would count as two arguments.
    """
    depth, current, parts = 0, '', []
    for character in text:
        if character in '[({':
            depth += 1
        elif character in '])}':
            depth -= 1
        if character == ',' and depth == 0:
            parts.append(current)
            current = ''
        else:
            current += character
    return [part.strip() for part in [*parts, current] if part.strip()]


def drawn(use_routing, routed=ROUTED):
    """
    The coordinates `routeThroughCol()` sets, run for real.

    The feature is a stand-in with the two methods the function uses: the routed shape it
    remembers, and a geometry that records what it is given instead of drawing it.
    """
    return evaluate('''(function () {
        let shape = null;
        const line = {
            get: key => key === 'routed_shape' ? %s : null,
            getGeometry: () => ({ setCoordinates: value => { shape = value; } }),
        };
        routeThroughCol(line, %s);
        return shape;
    })()''' % (json.dumps(routed), json.dumps(use_routing)), source=DETAIL_MAP_JS)


def redrawn(*switches):
    """Every shape `routeThroughCol()` sets as one feature is toggled, in order."""
    return evaluate("""(function () {
        const shapes = [];
        const line = {
            get: key => key === 'routed_shape' ? %s : null,
            getGeometry: () => ({ setCoordinates: value => { shapes.push(value); } }),
        };
        %s.forEach(routing => routeThroughCol(line, routing));
        return shapes;
    })()""" % (json.dumps(ROUTED), json.dumps(switches)), source=DETAIL_MAP_JS)


class RoutingTests(SimpleTestCase):
    def test_routing_on_draws_the_line_through_the_col(self):
        self.assertEqual(drawn(True), ROUTED)

    def test_routing_off_joins_the_peak_to_its_parent_directly(self):
        self.assertEqual(drawn(False), [ROUTED[0], ROUTED[-1]])

    def test_both_ends_survive_the_straightening(self):
        """The col is what goes; losing either endpoint would redraw a different line."""
        straight = drawn(False)
        self.assertEqual(straight[0], ROUTED[0])
        self.assertEqual(straight[-1], ROUTED[-1])

    def test_the_col_comes_back_when_routing_is_switched_on_again(self):
        """
        Which is why the routed shape is kept on the feature rather than read back off the
        geometry: once the middle vertex has been dropped, the only record of where the col
        was would be gone, and ticking the box again could not put it back.
        """
        self.assertEqual(redrawn(True, False, True), [ROUTED, [ROUTED[0], ROUTED[-1]], ROUTED])


class StartupTests(SimpleTestCase):
    def test_a_page_without_the_checkbox_still_gets_its_map(self):
        """
        The regression. `getElementById` returns null in the harness, which is exactly the
        comparison page's situation; reaching through it threw before the map was built.
        """
        self.assertEqual(evaluate('''(function () {
            const saved = fetch;
            fetch = () => ({ then: () => ({ then: () => null }) });
            try {
                initSummitMap('/summit/1/geo.json/', [20, 49]);
                return 'survived';
            } finally {
                fetch = saved;
            }
        })()''', source=DETAIL_MAP_JS), 'survived')

    def test_it_no_longer_builds_the_global_map_s_lineage_layer(self):
        """
        `buildLineageLayer()` reads `summits.features` and a mode; this page has neither.
        The detail collection already carries the line, drawn server-side.
        """
        self.assertNotIn('buildLineageLayer', read(DETAIL_MAP_JS))

    def test_every_call_site_passes_what_the_function_declares(self):
        """
        The drift that started all this: the file kept its old signature at both ends of a
        template boundary, where nothing checks and nothing throws.
        """
        declared = arguments(
            re.search(r'function initSummitMap\(([^)]*)\)', read(DETAIL_MAP_JS)).group(1))
        for template in CALLERS:
            with self.subTest(template=template):
                call = read(template).split('initSummitMap(')[1].split(');')[0]
                self.assertEqual(len(arguments(call)), len(declared))


class ServedShapeTests(TestCase):
    """
    The contract the straightening rests on: the line the server draws is three points, and
    the middle one is the key col. If `build_summit_features()` ever drew it as two
    segments, or put the col anywhere but the middle, the checkbox would quietly start
    cutting the wrong corner.
    """

    @classmethod
    def setUpTestData(cls):
        cls.parent = make_summit('Parent', 2600, 49.1, 20.1)
        cls.col = make_col('The Col', 1800, 48.98, 20.05)
        cls.summit = make_summit('Child', 2400, 49.0, 20.0,
                                 key_col=cls.col, prominence_parent=cls.parent)

    def line(self):
        response = self.client.get(f'/summit/{self.summit.pk}/geo.json/')
        self.assertEqual(response.status_code, 200)
        return next(f for f in response.json()['features']
                    if f['properties']['type'] == 'prominence_line')

    def test_the_line_is_drawn_through_the_col(self):
        coordinates = self.line()['geometry']['coordinates']
        self.assertEqual(len(coordinates), 3)
        self.assertEqual(coordinates[1], [self.col.point.location.x,
                                          self.col.point.location.y])

    def test_its_ends_are_the_summit_and_its_prominence_parent(self):
        coordinates = self.line()['geometry']['coordinates']
        self.assertEqual(coordinates[0], [self.summit.point.location.x,
                                          self.summit.point.location.y])
        self.assertEqual(coordinates[-1], [self.parent.point.location.x,
                                           self.parent.point.location.y])


class ScriptTests(SimpleTestCase):
    """
    What a page calling `initSummitMap` has to load for it to get anywhere.

    The comparison page loaded `map.js` and `summit-detail-map.js` but not `styles.js`, and
    `initSummitMap` hands `styleFor` — which lives in `styles.js` — to `makeMap`. The
    resulting `ReferenceError` is thrown inside a `.then()`, so it rejects the promise and
    goes nowhere: no map, no error on the page, nothing server-side to assert on. The page
    returned 200 with every feature correct and an empty square where the map belongs.

    A source sweep rather than a rendered one, so that a page added later is covered before
    anyone opens it. It assumes a template declares its own `{% block headextra %}`, which
    both callers do; a page that inherits its scripts from a parent would need this to
    follow the `{% extends %}` chain.
    """

    #: Everything `initSummitMap` reaches for: OpenLayers, `makeMap`, `styleFor`, itself.
    REQUIRED = ('ol@9/dist/ol.js', 'js/map.js', 'js/styles.js', 'js/summit-detail-map.js')

    def callers(self):
        return [path for path in templates()
                if 'initSummitMap(' in path.read_text(encoding='utf-8')]

    def test_both_maps_are_accounted_for(self):
        """The summit detail page and the comparison page — if one stops calling it, say so."""
        self.assertEqual(sorted(path.name for path in self.callers()),
                         ['comparison.html', 'detail.html'])

    def test_every_page_that_calls_it_loads_what_it_needs(self):
        missing = {}
        for path in self.callers():
            source = path.read_text(encoding='utf-8')
            if absent := [script for script in self.REQUIRED if script not in source]:
                missing[str(path)] = absent
        self.assertEqual(missing, {},
                         'initSummitMap fails silently inside a promise without these')
