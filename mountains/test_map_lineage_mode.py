"""
Which hierarchy the map draws between peaks, including the option to draw none.

The mode lives in three places that have to agree: the radio values in
`core/templates/core/controls.html`, `MAP_MODES` in `static/js/map.js` (what a URL fragment
is allowed to say), and `lineageParentAttr()` (what each mode actually draws towards). A
mode present in one and missing from another fails quietly — selectable but never
remembered, or remembered but drawing nothing — so the three are compared here.

`none` is the one whose correct behaviour is an *absence*, which is the hard kind to test.
Asserting that `lineageParentAttr('none')` is null only pins the decision; what is pinned
below is the consequence — the same summits that yield a line in every other mode yield no
features at all in this one — by running the real `buildLineageLayer()` against a counting
stand-in for OpenLayers.
"""

import re

from django.test import SimpleTestCase

from mountains.test_js_runtime import MAP_JS, call, evaluate

CONTROLS = 'core/templates/core/controls.html'

#: Two summits, the first naming the second as its parent in every hierarchy, and carrying
#: both of the things a routed line detours through: a key col (`kc`, matched against the
#: cols below) and a nearest higher point (`ilp`). Enough for a line in any mode that draws
#: one, in the shape `buildLineageLayer()` reads — the GeoJSON of the skeleton endpoint.
SUMMITS = {'features': [
    {'geometry': {'coordinates': [20.0, 49.0]},
     'properties': {'pk': 1, 'prominence_parent': 2, 'isolation_parent': 2,
                    'slope_parent': 2, 'horizon_parent': 2,
                    'kc': 7, 'ilp': {'lon': 20.05, 'lat': 49.05}}},
    {'geometry': {'coordinates': [20.1, 49.1]}, 'properties': {'pk': 2}},
]}
COLS = {'features': [{'geometry': {'coordinates': [20.02, 49.02]}, 'properties': {'pk': 7}}]}

DRAWING_MODES = ('prominence', 'isolation', 'slope', 'horizon')


def lineage_features(mode, routing=False):
    """
    How many line features `buildLineageLayer()` adds for `SUMMITS` in this mode.

    OpenLayers is swapped for a stand-in that counts instead of drawing — and swapped back
    in a `finally`, since the context is shared with every other test in the process. The
    function under test is the real one; only what it builds into is fake.
    """
    return evaluate('''(function () {
        const saved = ol;
        let added = 0;
        ol = {
            Feature: function (properties) { return properties; },
            source: { Vector: function () {
                return { addFeature: () => { added += 1; }, getFeatures: () => [] }; } },
            layer: { Vector: function (options) { return options; } },
            geom: { LineString: function (coordinates) { return coordinates; } },
            proj: { fromLonLat: coordinates => coordinates },
        };
        try {
            buildLineageLayer(%s, %s, %s, %s);
        } finally {
            ol = saved;
        }
        return added;
    })()''' % (_json(SUMMITS), _json(COLS), _json(mode), _json(routing)))


def _json(value):
    import json
    return json.dumps(value)


def radio_values():
    """The mode radios as the template declares them, in the order they are shown."""
    with open(CONTROLS, encoding='utf-8') as handle:
        return re.findall(r'<input type="radio" name="tree" value="([^"]+)"', handle.read())


class ModeVocabularyTests(SimpleTestCase):
    def test_the_radios_and_the_fragment_agree_on_the_modes(self):
        """
        Otherwise a mode is either unreachable or unrememberable: `parseMapState` drops a
        value it does not recognise, so a radio missing from `MAP_MODES` would be selectable
        and then silently lost on the next reload.
        """
        self.assertEqual(radio_values(), evaluate('MAP_MODES', source=MAP_JS))

    def test_none_is_one_of_them(self):
        self.assertIn('none', radio_values())
        self.assertIn('none', evaluate('MAP_MODES', source=MAP_JS))

    def test_the_radio_checked_in_the_template_is_the_declared_default(self):
        with open(CONTROLS, encoding='utf-8') as handle:
            checked = re.findall(r'name="tree" value="([^"]+)" checked', handle.read())
        self.assertEqual(checked, [evaluate('MAP_DEFAULTS.mode', source=MAP_JS)])

    def test_every_mode_is_a_decision_rather_than_a_gap(self):
        """
        `lineageParentAttr` must answer for each mode by name. A mode falling through to the
        default would still return null and still look like `none`, which is the one way
        this could break without anything visibly changing.
        """
        with open(MAP_JS, encoding='utf-8') as handle:
            body = handle.read().split('function lineageParentAttr')[1].split('\n}')[0]
        for mode in evaluate('MAP_MODES', source=MAP_JS):
            with self.subTest(mode=mode):
                self.assertIn(f"case '{mode}':", body)


class ParentAttributeTests(SimpleTestCase):
    def test_each_drawing_mode_names_its_own_parent(self):
        attributes = [call('lineageParentAttr', mode) for mode in DRAWING_MODES]
        self.assertEqual(attributes, ['prominence_parent', 'isolation_parent',
                                      'slope_parent', 'horizon_parent'])

    def test_none_names_no_parent_at_all(self):
        self.assertIsNone(call('lineageParentAttr', 'none'))

    def test_none_routes_through_nothing_either(self):
        """
        No key col ring and no isolation point: both exist to sit on a line, and there is
        no line. `lineageWaypoint` is what `rebuildOverlayLayers` and the hover both ask.
        """
        properties = {'kc': 7, 'ilp': {'lon': 20.0, 'lat': 49.0}}
        self.assertIsNone(call('lineageWaypoint', 'none', properties, True, {}))


class LineageLayerTests(SimpleTestCase):
    def test_every_other_mode_draws_a_line_for_the_same_summits(self):
        """The control: these fixtures do produce lineage, so an empty result means something."""
        for mode in DRAWING_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(lineage_features(mode), 1)

    def test_none_draws_nothing(self):
        self.assertEqual(lineage_features('none'), 0)

    def test_none_draws_nothing_with_routing_on_either(self):
        """
        Routing is a separate checkbox and stays wherever the reader left it. The two
        routed modes each add a second segment, which must not survive into `none`.
        """
        self.assertEqual(lineage_features('prominence', routing=True), 2)
        self.assertEqual(lineage_features('isolation', routing=True), 2)
        self.assertEqual(lineage_features('none', routing=True), 0)


class HoverTests(SimpleTestCase):
    """
    The highlight lives inside `initGlobalMap`'s closure and needs OpenLayers, so it stays
    source-asserted — but it is the one place that used to reach for a hierarchy the map was
    not showing, and in `none` mode that would draw the very line the mode hides.
    """

    def source(self):
        with open(MAP_JS, encoding='utf-8') as handle:
            return '\n'.join(line for line in handle
                             if not line.lstrip().startswith('//'))

    def test_the_hover_no_longer_falls_back_to_the_prominence_parent(self):
        self.assertNotIn("lineageParentAttr(modeName) || 'prominence_parent'", self.source())

    def test_the_hover_lights_up_a_parent_only_when_the_mode_has_one(self):
        self.assertIn('const parent = parentAttr && summitsByPk[feature.get(parentAttr)]',
                      self.source())
