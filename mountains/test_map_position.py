"""
Remembering what the reader had on screen.

The whole control state lives in the URL fragment — position, lineage mode, layer toggles
and base-map opacity — so a reload, or a pasted link, restores the view it describes:

    #map=11.25/49.16451/20.13403&mode=isolation&cols=1

Only what differs from the defaults is written, so the common case stays short. The parsing
and formatting are pure functions and are run here for real (see `test_js_runtime`); the DOM
half — which events record, `replaceState` versus `pushState`, the `hashchange` listener —
is beyond the harness's stubs and stays source-asserted at the bottom.

Parsing is the part most worth running, because a fragment is user-editable text arriving
from outside the application. A bad one must degrade to defaults, not open on empty ocean at
zoom 400 or drop the reader's position because one unrelated setting went stale.
"""

from django.test import SimpleTestCase

from mountains.test_js_runtime import call, evaluate

MAP_JS = 'static/js/map.js'


def parse(value):
    """`parseMapPosition` takes the value of the `map` key, not the whole fragment."""
    return call('parseMapPosition', value)


def fmt(zoom, lat, lon):
    return call('formatMapPosition', zoom, lat, lon)


def parse_state(fragment):
    return call('parseMapState', fragment)


def format_state(**state):
    import json
    return evaluate('formatMapState(%s)' % json.dumps(state))


class FormatTests(SimpleTestCase):
    def test_it_writes_the_openstreetmap_shape(self):
        self.assertEqual(fmt(8, 48.7, 19.7), '8.00/48.70000/19.70000')

    def test_zoom_keeps_two_decimals_because_openlayers_zooms_fractionally(self):
        self.assertEqual(fmt(8.3456, 48.7, 19.7), '8.35/48.70000/19.70000')

    def test_position_keeps_five_decimals_which_is_about_a_metre(self):
        self.assertEqual(fmt(8, 48.1234567, 19.7654321), '8.00/48.12346/19.76543')

    def test_it_handles_the_southern_and_western_hemispheres(self):
        self.assertEqual(fmt(3, -33.86785, -70.62667), '3.00/-33.86785/-70.62667')


class ParseTests(SimpleTestCase):
    def test_it_reads_back_what_it_wrote(self):
        self.assertEqual(parse('8.00/48.70000/19.70000'),
                         {'zoom': 8.0, 'lat': 48.7, 'lon': 19.7})

    def test_a_round_trip_is_stable(self):
        """What matters in practice: pan, reload, pan, reload — no drift."""
        once = fmt(11.25, 49.16451, 20.13403)
        position = parse(once)
        self.assertEqual(fmt(position['zoom'], position['lat'], position['lon']), once)

    def test_negative_coordinates_survive(self):
        self.assertEqual(parse('3.00/-33.86785/-70.62667'),
                         {'zoom': 3.0, 'lat': -33.86785, 'lon': -70.62667})

    def test_an_absent_fragment_is_not_a_position(self):
        for empty in ('', None):
            with self.subTest(fragment=empty):
                self.assertIsNone(parse(empty))

    def test_someone_elses_fragment_is_left_alone(self):
        """A bare anchor on the page must not be mistaken for a position."""
        for fragment in ('summits', 'map', '', 'zoom=8'):
            with self.subTest(fragment=fragment):
                self.assertIsNone(parse(fragment))

    def test_a_malformed_position_is_refused(self):
        for fragment in ('8/48.7', '8/48.7/19.7/3', 'a/b/c', '8//19.7',
                         '8/48.7/19.7extra'):
            with self.subTest(fragment=fragment):
                self.assertIsNone(parse(fragment))

    def test_out_of_range_values_are_refused(self):
        """Opening on the home extent beats opening on empty ocean at zoom 400."""
        for fragment in ('400.00/48.70000/19.70000',   # zoom
                         '-1.00/48.70000/19.70000',
                         '8.00/91.00000/19.70000',     # latitude
                         '8.00/-91.00000/19.70000',
                         '8.00/48.70000/181.00000',    # longitude
                         '8.00/48.70000/-181.00000'):
            with self.subTest(fragment=fragment):
                self.assertIsNone(parse(fragment))

    def test_the_extremes_themselves_are_accepted(self):
        self.assertIsNotNone(parse('0.00/90.00000/180.00000'))
        self.assertIsNotNone(parse('22.00/-90.00000/-180.00000'))


class StateFormatTests(SimpleTestCase):
    """The whole fragment: position plus mode, layer toggles and opacity."""

    HERE = {'zoom': 11.25, 'lat': 49.16451, 'lon': 20.13403}

    def test_defaults_are_left_out_so_the_common_case_stays_short(self):
        self.assertEqual(format_state(position=self.HERE, mode='prominence', routing=True,
                                      rivers=True, cols=False, ranges=False, opacity=40),
                         '#map=11.25/49.16451/20.13403')

    def test_only_what_differs_is_written(self):
        self.assertEqual(format_state(position=self.HERE, mode='isolation', routing=True,
                                      rivers=True, cols=True, ranges=False, opacity=40),
                         '#map=11.25/49.16451/20.13403&mode=isolation&cols=1')

    def test_a_toggle_turned_off_is_written_as_well_as_one_turned_on(self):
        self.assertIn('rivers=0', format_state(position=None, mode='prominence', routing=True,
                                               rivers=False, cols=False, ranges=False,
                                               opacity=40))

    def test_an_untouched_map_writes_nothing_at_all(self):
        """So the address bar stays clean until the reader actually changes something."""
        self.assertEqual(format_state(position=None, mode='prominence', routing=True,
                                      rivers=True, cols=False, ranges=False, opacity=40), '')

    def test_the_whole_state_round_trips(self):
        state = dict(position=self.HERE, mode='horizon', routing=False, rivers=False,
                     cols=True, ranges=True, opacity=85)
        self.assertEqual(parse_state(format_state(**state)), state)


class StateParseTests(SimpleTestCase):
    def test_an_empty_fragment_is_all_defaults(self):
        self.assertEqual(parse_state(''), {
            'position': None, 'mode': 'prominence', 'routing': True, 'rivers': True,
            'cols': False, 'ranges': False, 'opacity': 40,
        })

    def test_a_position_only_fragment_still_reads(self):
        """Every link written before the other settings existed."""
        state = parse_state('#map=8.00/48.70000/19.70000')
        self.assertEqual(state['position'], {'zoom': 8.0, 'lat': 48.7, 'lon': 19.7})
        self.assertEqual(state['mode'], 'prominence')

    def test_each_mode_is_accepted(self):
        for mode in ('prominence', 'isolation', 'slope', 'horizon'):
            with self.subTest(mode=mode):
                self.assertEqual(parse_state(f'#mode={mode}')['mode'], mode)

    def test_an_unknown_mode_falls_back_without_losing_the_position(self):
        """A stale link with one obsolete setting should still go to the right place."""
        state = parse_state('#map=8.00/48.70000/19.70000&mode=encirclement')
        self.assertEqual(state['mode'], 'prominence')
        self.assertIsNotNone(state['position'])

    def test_toggles_read_one_and_zero_and_nothing_else(self):
        self.assertTrue(parse_state('#cols=1')['cols'])
        self.assertFalse(parse_state('#rivers=0')['rivers'])
        # anything else leaves the default alone rather than guessing
        for junk in ('#cols=true', '#cols=yes', '#cols='):
            with self.subTest(fragment=junk):
                self.assertFalse(parse_state(junk)['cols'])

    def test_opacity_is_bounded(self):
        self.assertEqual(parse_state('#opacity=0')['opacity'], 0)
        self.assertEqual(parse_state('#opacity=100')['opacity'], 100)
        for junk in ('#opacity=101', '#opacity=-1', '#opacity=dark'):
            with self.subTest(fragment=junk):
                self.assertEqual(parse_state(junk)['opacity'], 40)

    def test_unknown_keys_are_ignored(self):
        state = parse_state('#map=8.00/48.70000/19.70000&colour=blue&mode=slope')
        self.assertEqual(state['mode'], 'slope')
        self.assertIsNotNone(state['position'])

    def test_a_fragment_from_somewhere_else_yields_plain_defaults(self):
        for fragment in ('#summits', '#section-3', '#'):
            with self.subTest(fragment=fragment):
                state = parse_state(fragment)
                self.assertIsNone(state['position'])
                self.assertEqual(state['mode'], 'prominence')


class WiringTests(SimpleTestCase):
    """
    The DOM half, which the JS harness cannot run — `map.on(...)`, `window.history`,
    `dispatchEvent` and OpenLayers projections are all beyond its stubs.
    """

    def source(self):
        """
        The file with `//` comment lines dropped — the comments explain *why* `pushState` is
        wrong, and an assertion that the file never says the word would trip over them.
        """
        with open(MAP_JS, encoding='utf-8') as handle:
            return '\n'.join(line for line in handle
                             if not line.lstrip().startswith('//'))

    def test_moving_the_map_records_the_state(self):
        self.assertIn("map.on('moveend', rememberMapState)", self.source())

    def test_every_control_records_it_too(self):
        """A setting that is not written on change is lost on the next reload."""
        source = self.source()
        self.assertIn("r.addEventListener('change', rememberMapState)", source)
        self.assertIn("box.addEventListener('change', rememberMapState)", source)
        # `input`, not `change`: a slider that only recorded on release would lose the
        # setting of anyone who drags and then reloads.
        self.assertIn("opacityControl.addEventListener('input', rememberMapState)", source)

    def test_it_replaces_rather_than_pushes_history(self):
        """
        `pushState`, or assigning to `location.hash`, would leave one history entry per pan
        and bury whatever page the reader arrived from.
        """
        source = self.source()
        self.assertIn('window.history.replaceState', source)
        self.assertNotIn('pushState', source)
        self.assertNotIn('location.hash =', source)

    def test_a_remembered_state_wins_over_the_home_extent(self):
        source = self.source()
        resumed = (source.split('const resumed = parseMapState')[1]
                         .split('renderProminenceLegend')[0])
        self.assertIn('applyMapControls(resumed, false)', resumed)
        self.assertIn('applyMapPosition(resumed.position)', resumed)
        self.assertIn('HOME_EXTENT', resumed)   # still the fallback

    def test_the_controls_are_restored_without_notifying_during_startup(self):
        """
        The layers are built from the controls directly, so setting them is enough — and
        dispatching events at layers that do not exist yet would not be.
        """
        self.assertIn('applyMapControls(resumed, false)', self.source())

    def test_an_externally_edited_fragment_is_followed(self):
        """Pasting a link into an already-open tab should move the map, not need a reload."""
        source = self.source()
        handler = source.split("addEventListener('hashchange'")[1].split('});')[0]
        self.assertIn('applyMapControls(wanted, true)', handler)
        self.assertIn('applyMapPosition(wanted.position)', handler)

    def test_the_opacity_slider_is_applied_on_load(self):
        """Nothing else does it: the tile layer's opacity is only set by the input listener."""
        self.assertIn('tileLayer.setOpacity(resumed.opacity / 100)', self.source())
