"""
Which summit a map shows from which zoom (`core.functions.declutter`), and the browser half of
it: a parent never shows later than its child (`visibleZooms` in static/js/map.js).
"""
from django.test import SimpleTestCase

from core.functions.declutter import first_zooms
from mountains.test_js_runtime import call


def zooms(*summits, **kw):
    return first_zooms(summits, **kw)


class FirstZoomTests(SimpleTestCase):
    def test_the_more_prominent_of_two_neighbours_shows_first(self):
        z = zooms(('big', 20.0, 49.0, 900, 1500), ('small', 20.01, 49.0, 100, 1600))
        self.assertEqual(z['big'], 0)
        self.assertGreater(z['small'], z['big'])

    def test_a_lone_low_hill_shows_from_far_out(self):
        """Nothing more prominent near it on screen: isolation counts without being measured."""
        crowd = [(f'tatra{i}', 20.0 + i * 0.01, 49.2, 500 + i, 2000) for i in range(10)]
        z = zooms(*crowd, ('plain', 21.5, 48.0, 50, 300))
        # Out before most of the crowd, though each of them is ten times as prominent.
        self.assertLess(z['plain'], sorted(z[f'tatra{i}'] for i in range(10))[1])

    def test_what_shows_once_keeps_showing(self):
        """Neighbours separate as the zoom grows: a summit's zoom is a threshold, not a band."""
        z = zooms(*[(i, 20 + (i % 7) * 0.003, 49 + (i // 7) * 0.003, 1000 - i, 1000) for i in range(49)])
        self.assertEqual(sorted(z), list(range(49)))
        self.assertTrue(all(0 <= v <= 18 for v in z.values()))

    def test_a_lesser_summit_never_keeps_a_greater_one_hidden(self):
        """
        Cachi (P2150) waits for Chañi (P2158) to move far enough away on screen; Morro del
        Quemado (P687), right beside Cachi and far from Chañi, must not show first.
        """
        z = zooms(('chani', -65.74, -24.06, 2158, 5896), ('cachi', -66.39, -24.93, 2150, 6380),
                  ('quemado', -66.405, -24.832, 687, 5000))
        self.assertLessEqual(z['cachi'], z['quemado'])

    def test_what_is_drawn_at_a_level_keeps_its_spacing(self):
        import math, random
        from core.functions.declutter import SPACING, mercator
        rnd = random.Random(1)
        pts = [(i, 19 + rnd.random() * 2, 48 + rnd.random(), rnd.random() * 1000, 500) for i in range(400)]
        z = first_zooms(pts)
        xy = {k: mercator(lon, lat) for k, lon, lat, _, _ in pts}
        for level in range(0, 19, 3):
            drawn = [k for k in z if z[k] <= level]
            for i, a in enumerate(drawn):
                for b in drawn[i + 1:]:
                    d = math.dist(xy[a], xy[b]) * 2 ** level
                    self.assertGreaterEqual(d, SPACING - 1e-9, (level, a, b))

    def test_two_tops_on_one_spot_never_part_and_the_lesser_waits_for_the_last_zoom(self):
        z = zooms(('a', 20.0, 49.0, 300, 900), ('b', 20.0, 49.0, 200, 900))
        self.assertEqual((z['a'], z['b']), (0, 18))

    def test_unknown_prominence_comes_last(self):
        z = zooms(('known', 20.0, 49.0, 30, 500), ('unknown', 20.001, 49.0, None, 900))
        self.assertLess(z['known'], z['unknown'])

    def test_the_higher_wins_a_tie(self):
        z = zooms(('low', 20.0, 49.0, 300, 900), ('high', 20.001, 49.0, 300, 950))
        self.assertLess(z['high'], z['low'])


class VisibleZoomTests(SimpleTestCase):
    """
    A drawn summit keeps its real parent in the hierarchy on show, drawn too: never a line
    re-attached to a grandparent because the parent is hidden at that zoom.
    """

    def vz(self, *summits):
        return {int(k): v for k, v in call('visibleZooms',
                                           [dict(pk=pk, z=z, parent=p) for pk, z, p in summits]).items()}

    def test_a_parent_shows_as_early_as_its_earliest_child(self):
        self.assertEqual(self.vz((1, 9, None), (2, 5, 1), (3, 7, 1)), {1: 5, 2: 5, 3: 7})

    def test_the_whole_chain_up_is_lowered(self):
        self.assertEqual(self.vz((1, 8, None), (2, 9, 1), (3, 4, 2)), {1: 4, 2: 4, 3: 4})

    def test_whatever_is_drawn_has_its_parent_drawn(self):
        summits = [(1, 6, None), (2, 9, 1), (3, 3, 2), (4, 12, 3), (5, 2, 4), (6, 8, 1)]
        vz = self.vz(*summits)
        for pk, _, parent in summits:
            if parent is not None:
                self.assertLessEqual(vz[parent], vz[pk])

    def test_an_earlier_parent_is_left_alone(self):
        self.assertEqual(self.vz((1, 2, None), (2, 6, 1)), {1: 2, 2: 6})

    def test_a_parent_not_on_the_map_is_no_obstacle(self):
        self.assertEqual(self.vz((2, 6, 99),), {2: 6})

    def test_a_cycle_does_not_hang(self):
        self.assertEqual(self.vz((1, 5, 2), (2, 3, 1)), {1: 3, 2: 3})
