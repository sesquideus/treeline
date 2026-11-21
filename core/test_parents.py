"""
`core.functions.parents`: the branch-and-bound slope and horizon parents must be exactly those
of the exhaustive search the admin actions used to run (every summit against every other).
"""
import math
import random

from django.test import SimpleTestCase
from geographiclib.geodesic import Geodesic

from core.functions.parents import angle, horizon_parents, slope, slope_parents


def brute(summits, score, positive):
    out = {}
    for k, lat, lon, h in summits:
        best, best_score = None, -math.inf
        for q, qlat, qlon, qh in summits:
            if q == k or qh <= h:
                continue
            s = score(h, qh, Geodesic.WGS84.Inverse(lat, lon, qlat, qlon)['s12'])
            if s > best_score:
                best, best_score = q, s
        if best is None or (positive and best_score <= 0):
            out[k] = None
        else:
            out[k] = best
    return out


def scatter(seed, n, lat=(-90, 90), lon=(-180, 180), cluster=None):
    rnd = random.Random(seed)
    out = []
    for k in range(n):
        if cluster:
            clat, clon, spread = cluster
            la, lo = clat + rnd.gauss(0, spread), clon + rnd.gauss(0, spread)
            if abs(la) > 90:          # over the pole, not piled up on it
                la, lo = math.copysign(180, la) - la, lo + 180
            lo = (lo + 180) % 360 - 180
        else:
            la, lo = rnd.uniform(*lat), rnd.uniform(*lon)
        out.append((k, la, lo, round(rnd.uniform(0, 5000), 1)))
    return out


class ExactTests(SimpleTestCase):
    def check(self, summits):
        mine = {k: p for k, (p, _) in slope_parents(summits).items()}
        self.assertEqual(mine, brute(summits, slope, positive=False), 'slope parents')
        for k in (0.0, 0.14):
            mine = {key: p for key, (p, _) in horizon_parents(summits, refraction=k).items()}
            self.assertEqual(mine, brute(summits, angle(k), positive=True), f'horizon parents at k={k}')

    def test_the_whole_globe(self):
        for seed in range(3):
            with self.subTest(seed=seed):
                self.check(scatter(seed, 300))

    def test_a_dense_range(self):
        for seed in range(3):
            with self.subTest(seed=seed):
                self.check(scatter(seed, 400, cluster=(49.2, 20.0, 0.3)))

    def test_across_the_antimeridian_and_at_a_pole(self):
        for cluster in ((65.0, 180.0, 2.0), (-89.0, 0.0, 1.5), (89.5, 45.0, 0.5)):
            with self.subTest(cluster=cluster):
                self.check(scatter(7, 300, cluster=cluster))

    def test_equal_heights_and_the_highest_summit(self):
        summits = scatter(3, 200, cluster=(46.5, 9.0, 0.5))
        summits = [(k, la, lo, float(int(h / 500) * 500)) for k, la, lo, h in summits]   # many ties
        top = max(summits, key=lambda s: s[3])[0]
        result = slope_parents(summits)
        self.assertIsNone(result[top][0])
        for k, (p, _) in result.items():
            if p is not None:
                self.assertGreater(dict((s[0], s[3]) for s in summits)[p], dict((s[0], s[3]) for s in summits)[k])

    def test_only_some(self):
        summits = scatter(5, 300, cluster=(46.5, 9.0, 1.0))
        some = slope_parents(summits, keys=[1, 2, 3])
        self.assertEqual(set(some), {1, 2, 3})
        self.assertEqual({k: p for k, (p, _) in some.items()},
                         {k: p for k, p in brute(summits, slope, False).items() if k in (1, 2, 3)})
