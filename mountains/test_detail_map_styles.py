"""
The summit page's map in the global map's colours.

Every peak on it is drawn as the global map draws a summit — its prominence band's colour
and size — whatever its relation to the page's own: a child, the nearest higher neighbour,
the encirclement parent. Only the parent stands apart, lit as the global map lights it on
hover. And every line is the global map's line for the same relation: a climb is
purple → cyan through the key col, cyan → turquoise on to the parent; an isolation leg is
red → magenta out to the nearest higher point, magenta → blue on to the neighbour.

`styleFor()` runs in QuickJS (see `test_js_runtime`), with the two primitives it draws
through — `summitMarker()` and `segmentStyles()` — swapped for recorders for the one call,
since the stubbed OpenLayers keeps nothing that could be read back.
"""

import json

from django.test import SimpleTestCase

from mountains.test_js_runtime import STYLES_JS, evaluate


def drawn(properties, coordinates=((0, 0), (1, 1), (2, 2))):
    """What `styleFor()` asks for: each marker's colour, radius and z, each gradient's ends."""
    return evaluate('''(function () {
        const record = [];
        const marker = summitMarker, segments = segmentStyles;
        summitMarker = (colour, radius, options) => {
            record.push({ marker: colour, radius, z: (options || {}).zIndex });
            return {};
        };
        segmentStyles = (coords, from, to) => { record.push({ from, to }); return [{}]; };
        try {
            styleFor({ get: key => (%s)[key],
                       getGeometry: () => ({ getCoordinates: () => %s }) });
            return record;
        } finally {
            summitMarker = marker;
            segmentStyles = segments;
        }
    })()''' % (json.dumps(properties), json.dumps(coordinates)), source=STYLES_JS)


def constant(name):
    return evaluate(name, source=STYLES_JS)


class SummitMarkerTests(SimpleTestCase):
    def test_every_peak_is_drawn_as_a_summit_of_its_band(self):
        for prom in (12.0, 450.0, 2100.0):
            summit = drawn({'type': 'summit', 'prom': prom})
            for kind in ('prominence_child', 'isolation_parent', 'encirclement_parent',
                         'key_for_summit'):
                with self.subTest(kind=kind, prom=prom):
                    self.assertEqual(drawn({'type': kind, 'prom': prom}), summit)

    def test_the_band_is_the_one_the_legend_shows(self):
        [marker] = drawn({'type': 'prominence_child', 'prom': 2100.0})
        self.assertEqual(marker['marker'], evaluate('prominenceBand(2100).colour',
                                                    source=STYLES_JS))


class LineTests(SimpleTestCase):
    def ends(self, *names):
        return [constant(name) for name in names]

    def test_a_routed_climb_is_the_global_maps_two_segments(self):
        for kind in ('prominence_line', 'child_line'):
            with self.subTest(kind=kind):
                self.assertEqual(drawn({'type': kind}), [
                    dict(zip(('from', 'to'), self.ends('PROMINENCE_PEAK_TO_COL_A',
                                                       'PROMINENCE_PEAK_TO_COL_B'))),
                    dict(zip(('from', 'to'), self.ends('PROMINENCE_COL_TO_PARENT_A',
                                                       'PROMINENCE_COL_TO_PARENT_B'))),
                ])

    def test_a_straightened_climb_runs_end_to_end(self):
        self.assertEqual(drawn({'type': 'prominence_line'}, ((0, 0), (2, 2))), [
            dict(zip(('from', 'to'), self.ends('PROMINENCE_PEAK_TO_COL_A',
                                               'PROMINENCE_COL_TO_PARENT_B'))),
        ])

    def test_the_isolation_legs_are_the_global_maps(self):
        self.assertEqual(drawn({'type': 'isolation_line_first'}, ((0, 0), (1, 1))), [
            dict(zip(('from', 'to'), self.ends('ISOLATION_PEAK_TO_NHP_A',
                                               'ISOLATION_PEAK_TO_NHP_B'))),
        ])
        self.assertEqual(drawn({'type': 'isolation_line_second'}, ((1, 1), (2, 2))), [
            dict(zip(('from', 'to'), self.ends('ISOLATION_NHP_TO_PARENT_A',
                                               'ISOLATION_NHP_TO_PARENT_B'))),
        ])

    def test_a_key_col_line_is_the_first_half_of_a_climb(self):
        self.assertEqual(drawn({'type': 'key_col_line'}, ((0, 0), (1, 1))), [
            dict(zip(('from', 'to'), self.ends('PROMINENCE_PEAK_TO_COL_A',
                                               'PROMINENCE_PEAK_TO_COL_B'))),
        ])

    def test_the_global_map_reads_the_same_constants(self):
        """One definition: map.js no longer declares a copy that could drift."""
        with open('static/js/map.js', encoding='utf-8') as handle:
            source = handle.read()
        for name in ('PROMINENCE_PEAK_TO_COL_A', 'ISOLATION_PEAK_TO_NHP_A',
                     'SLOPE_COLOUR_A', 'HORIZON_COLOUR_A'):
            with self.subTest(name=name):
                self.assertNotIn(f'const {name}', source)
                self.assertIn(name, source)


class WatershedHighPointTests(SimpleTestCase):
    def marks(self, properties):
        """ Which primitive draws it: `summitMarker()` or `dot()`, swapped for recorders. """
        return evaluate('''(function () {
            const record = [];
            const marker = summitMarker, point = dot;
            summitMarker = colour => { record.push(['summit', colour]); return {}; };
            dot = colour => { record.push(['dot', colour]); return {}; };
            try {
                styleFor({ get: key => (%s)[key], getGeometry: () => null });
                return record;
            } finally {
                summitMarker = marker;
                dot = point;
            }
        })()''' % json.dumps(properties), source=STYLES_JS)

    def test_a_catalogued_summit_is_drawn_as_a_summit(self):
        self.assertEqual(self.marks({'type': 'watershed_high_point', 'pk': 5, 'prom': 300}),
                         [['summit', '#d35400']])

    def test_a_bare_named_point_is_a_purple_dot(self):
        self.assertEqual(self.marks({'type': 'watershed_high_point', 'label': '⩓ x'}),
                         [['dot', '#8e44ad']])
