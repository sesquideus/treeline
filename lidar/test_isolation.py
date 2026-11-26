"""
Tests of the isolation search over any terrain (isolation.search, refine: the nearest point where
the bilinear surface rises above a summit), the climb to the isolation parent (nhn.Climb), and the
lidar reader through a tile index (terrain.LidarTerrain), on synthetic grids where the answer is known.

    cd lidar && uv run --project .. --group lidar python -m unittest test_isolation.py
"""
import importlib
import math
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tileindex import write_cm  # noqa: E402

LAT, LON = 48.0, 24.0
MLAT = 111132.954 - 559.822 * math.cos(2 * math.radians(LAT))          # m per degree of latitude there
MLON = math.pi / 180 * 6378137.0 * math.cos(math.radians(LAT)) / math.sqrt(1 - 0.00669438 * math.sin(math.radians(LAT)) ** 2)


def geodesic(lon0, lat0, lon1, lat1):
    from pyproj import Geod
    return Geod(ellps='WGS84').inv(lon0, lat0, lon1, lat1)[2]


def grid(cell, rows, cols, north=LAT + 0.01, west=LON - 0.01):
    """ Cell centres of a grid of `cell` metres: lon, lat (2-D) and the steps in degrees. """
    dlat, dlon = cell / MLAT, cell / MLON
    lat = north - np.arange(rows)[:, None] * dlat + 0 * np.arange(cols)[None, :]
    lon = west + np.arange(cols)[None, :] * dlon + 0 * np.arange(rows)[:, None]
    return lon, lat, dlon, dlat, north, west


class Refinement(unittest.TestCase):
    """ The nearest higher point on the continuous surface, against the cell-centre answer. """

    def plane(self, cell, k, t):
        """ z rises eastwards, linear in longitude (so bilinear is exact): contour z = t a meridian. """
        import terrain
        from isolation import search
        n = int(1.6 * t / k / cell) + 40
        lon, lat, dlon, dlat, north, west = grid(cell, n // 2, n, north=LAT + n / 4 * cell / MLAT,
                                                 west=LON - 20 * cell / MLON)
        z = k * (lon - LON) * MLON
        T = terrain.ArrayTerrain(z, west, north, dlon, dlat)
        r = search(T, LON, LAT, t)
        lon_c = LON + t / k / MLON
        return r, geodesic(LON, LAT, lon_c, LAT)

    def test_plane_on_1m_cells(self):
        r, want = self.plane(1.0, 0.1, 37.337)
        self.assertAlmostEqual(r['metres'], want, delta=0.02)
        self.assertGreaterEqual(r['centre_m'], r['metres'] - 1e-6)

    def test_plane_on_30m_cells(self):
        r, want = self.plane(30.0, 0.01, 37.337)
        self.assertAlmostEqual(r['metres'], want, delta=0.1)
        # a cell centre is up to a cell off; the surface is not
        self.assertGreater(r['centre_m'] - want, 1.0)

    def cone(self, cell, H, s, ax, ay, t, size):
        """ z = H - s * (metres from the apex at (ax east, ay north) of the summit): a circle at H - t. """
        import terrain
        from isolation import search
        n = int(size / cell)
        lon, lat, dlon, dlat, north, west = grid(cell, n, n, north=LAT + (size / 2) / MLAT, west=LON - (size / 4) / MLON)
        alon, alat = LON + ax / MLON, LAT + ay / MLAT
        from pyproj import Geod
        d = Geod(ellps='WGS84').inv(np.full(lon.size, alon), np.full(lon.size, alat), lon.ravel(), lat.ravel())[2]
        z = (H - s * d).reshape(lon.shape)
        T = terrain.ArrayTerrain(z, west, north, dlon, dlat)
        r = search(T, LON, LAT, t)
        return r, geodesic(LON, LAT, alon, alat) - (H - t) / s

    def test_cone_on_1m_cells(self):
        r, want = self.cone(1.0, 300.0, 0.5, 240.0, 180.0, 250.0, 600)
        self.assertAlmostEqual(r['metres'], want, delta=0.03)

    def test_cone_on_30m_cells(self):
        r, want = self.cone(30.0, 1000.0, 0.2, 4000.0, 3000.0, 800.0, 12000)
        self.assertAlmostEqual(r['metres'], want, delta=0.15)
        self.assertGreater(r['centre_m'] - r['metres'], 0.5)

    def test_contour_samples_lie_on_the_surface(self):
        from isolation import contour_samples
        z = np.array([[0.0], [10.0], [3.0], [-4.0]])
        v, u, ok = contour_samples(z[0], z[1], z[2], z[3], 2.5)
        f = z[0, 0] + (z[1, 0] - z[0, 0]) * u + (z[2, 0] - z[0, 0]) * v + (z[3, 0] - z[2, 0] - z[1, 0] + z[0, 0]) * u * v
        self.assertTrue((f[ok] >= 2.5 - 1e-6).all())
        self.assertTrue(ok.sum() > 50)


class Search(unittest.TestCase):

    def terrain(self, z, cell=10.0):
        import terrain
        lon, lat, dlon, dlat, north, west = grid(cell, *z.shape)
        return terrain.ArrayTerrain(z, west, north, dlon, dlat, block=8), lon, lat

    def test_far_and_near_blocks(self):
        """ The nearer of two higher spots wins though the farther one is far higher; a block whose
        maximum is not higher is never read. """
        from isolation import search
        z = np.zeros((200, 200))
        z[100, 100] = 50.0                  # the summit
        z[100, 140] = 51.0                  # 400 m east, barely higher
        z[100, 20] = 900.0                  # 800 m west
        T, lon, lat = self.terrain(z)
        r = search(T, lon[100, 100], lat[100, 100], 50.01)
        self.assertAlmostEqual(r['lon'], lon[100, 139] + (lon[100, 140] - lon[100, 139]) * (50.01 / 51.0), places=6)
        self.assertIsNone(search(T, lon[20, 20], lat[20, 20], 901.0))

    def test_square_without_data_keeps_the_centre(self):
        from isolation import search
        z = np.full((60, 60), np.nan)
        z[30, 30] = 10.0
        z[30, 45] = 20.0                    # higher, alone among no data: its centre is the answer
        T, lon, lat = self.terrain(z)
        r = search(T, lon[30, 30], lat[30, 30], 10.01)
        self.assertAlmostEqual(r['lon'], lon[30, 45], places=9)
        self.assertAlmostEqual(r['metres'], r['centre_m'], places=6)


class ClimbTests(unittest.TestCase):
    """
    A ridge along row 100 (10 m cells): S (200 m) at column 100, a dip to 190, a bump B (205 m)
    at 130, a col at 198 or 190, and P (300 m) at 170. Q (300 m), north of B across a deep
    valley, is nearer to S's nearest higher point than P is.
    """

    def build(self, col):
        import terrain
        c = np.arange(260, dtype=np.float64)
        prof = np.interp(c, [0, 100, 110, 130, 140, 170, 259], [0, 200, 190, 205, col, 300, 0])
        r = np.arange(200, dtype=np.float64)[:, None]
        z = prof[None, :] - 3.0 * np.abs(r - 100)
        q = 300.0 - 10.0 * np.hypot(r - 60, c[None, :] - 128)
        z = np.maximum(np.maximum(z, q), 0.0)
        lon, lat, dlon, dlat, north, west = grid(10.0, *z.shape)
        T = terrain.ArrayTerrain(z, west, north, dlon, dlat, block=8)
        summits = {k: dict(pk=i, point=dict(name=k, lon=float(lon[rc]), lat=float(lat[rc]), alt=float(z[rc])))
                   for i, (k, rc) in enumerate([('S', (100, 100)), ('B', (100, 130)), ('P', (100, 170)),
                                                ('Q', (60, 128))])}
        return T, summits

    def climb(self, T, summits, catalogued):
        import isolation_run
        from isolation import search
        from nhn import Climb
        s = summits['S']
        isolation_run.G['summits'] = isolation_run.Summits([summits[k] for k in catalogued])
        r = search(T, s['point']['lon'], s['point']['lat'], s['point']['alt'] + 0.01)
        hit, info = Climb(T.frame(), cutoff=10.0, size=64, max_size=512).run(
            r['anchor'][0], r['anchor'][1], isolation_run.matcher(s, 100.0, 40.0))
        return r, hit, info

    def test_a_bump_is_crossed_not_the_valley(self):
        T, S = self.build(198.0)                        # B dips 7 m: a bump on the ridge
        r, hit, info = self.climb(T, S, 'SBPQ')
        self.assertEqual(hit['point']['name'], 'P')
        d_q = geodesic(r['lon'], r['lat'], S['Q']['point']['lon'], S['Q']['point']['lat'])
        d_p = geodesic(r['lon'], r['lat'], S['P']['point']['lon'], S['P']['point']['lat'])
        self.assertLess(d_q, d_p)                       # Q was nearer, and not taken

    def test_a_catalogued_top_of_ten_metres_is_the_parent(self):
        T, S = self.build(190.0)                        # B dips 15 m
        self.assertEqual(self.climb(T, S, 'SBPQ')[1]['point']['name'], 'B')

    def test_an_uncatalogued_top_is_climbed_past(self):
        T, S = self.build(190.0)
        self.assertEqual(self.climb(T, S, 'SPQ')[1]['point']['name'], 'P')

    def test_a_top_lower_in_the_database_is_climbed_past(self):
        T, S = self.build(190.0)
        S['B']['point']['alt'] = 199.0                  # the database has B below S
        self.assertEqual(self.climb(T, S, 'SBPQ')[1]['point']['name'], 'P')

    def test_flood_statuses(self):
        from nhn import HIGHER, NO_DATA, PROMINENT, flood_from
        z = np.array([[0, 0, 0, 0, 0, 0, 0],
                      [0, 5, 3, 9, 1, 1, 0],
                      [0, 1, 1, 1, 1, 1, 0],
                      [0, 0, 0, 0, 0, 0, 0]], np.float32)
        w, h = 7, 4
        st, q, level = flood_from(z.ravel(), w, h, 1 * w + 1, 10.0, False, 1000)
        self.assertEqual((st, q, level), (HIGHER, 1 * w + 3, 3.0))
        st, _, _ = flood_from(z.ravel(), w, h, 1 * w + 3, 5.0, False, 1000)
        self.assertEqual(st, PROMINENT)
        z[1, 2] = np.nan
        st, _, _ = flood_from(z.ravel(), w, h, 1 * w + 1, 10.0, False, 1000)
        self.assertEqual(st, NO_DATA)


class Lidar(unittest.TestCase):
    """ LidarTerrain over a temporary data root: int-cm tiles, their index, heights to EGM2008. """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['TREELINE_DATA'] = self.tmp.name
        os.environ['TREELINE_ISOLATION_CACHE'] = os.path.join(self.tmp.name, 'cache')
        import data, sources, tileindex, terrain
        for m in (data, sources, tileindex, terrain):
            importlib.reload(m)
        self.terrain, self.tileindex, self.sources = terrain, tileindex, sources
        d = os.path.join(self.tmp.name, 'intcm', 'DE', 'NW', 'tiles')
        os.makedirs(d)
        # a plane rising 0.1 m a metre eastwards over two 1 km tiles of ETRS89 / UTM 32N
        for e in (530, 531):
            x = e * 1000 + 0.5 + np.arange(1000)[None, :] + 0 * np.arange(1000)[:, None]
            write_cm(os.path.join(d, f'{e}_5744.tif'), 100.0 + 0.1 * (x - 530000), e * 1000, 5745000)
        tileindex.build(sources.BY_KEY['DE-NW'], 1)

    def tearDown(self):
        os.environ.pop('TREELINE_DATA')
        os.environ.pop('TREELINE_ISOLATION_CACHE')
        self.tmp.cleanup()
        import data, sources, tileindex, terrain
        for m in (data, sources, tileindex, terrain):
            importlib.reload(m)

    def test_nearest_higher_point_across_tiles_on_egm2008(self):
        from isolation import search
        from pyproj import Transformer
        T = self.terrain.LidarTerrain(keys=['DE-NW'], log=lambda *a: None)
        tr = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True)
        lon, lat = tr.transform(530200.0, 5744500.0)
        corr = self.terrain.egm_correction('EPSG:4258+7837', [lon], [lat])[0][0]
        top = T.top(lon, lat, 15.0)
        self.assertAlmostEqual(top[0], 100.0 + 0.1 * 214.5 + corr, delta=0.02)      # 15 m east, cell centre
        t = 100.0 + 0.1 * 950.0 + corr                                          # x = 530950: across into 531
        r = search(T, lon, lat, t)
        self.assertEqual(r['source'], 'DE-NW')
        e, n = Transformer.from_crs('EPSG:4326', 'EPSG:25832', always_xy=True).transform(r['lon'], r['lat'])
        self.assertAlmostEqual(e, 530950.0, delta=0.05)
        self.assertAlmostEqual(n, 5744500.0, delta=0.05)
        self.assertAlmostEqual(r['metres'], geodesic(lon, lat, *tr.transform(530950.0, 5744500.0)), delta=0.05)
        # and beyond the second tile nothing is higher
        self.assertIsNone(search(T, lon, lat, 100.0 + 0.1 * 2001 + corr))

    def test_top_walks_up_at_full_resolution(self):
        """ A point 30 m off its top walks up to it; a tower 8 m before a higher face stays its own top. """
        from pyproj import Transformer
        d = os.path.join(self.tmp.name, 'intcm', 'DE', 'NW', 'tiles')
        yy, xx = np.mgrid[0:1000, 0:1000]
        z = 500.0 - 0.2 * np.hypot(yy - 500, xx - 500)             # a cone, its top at the centre
        z[200, 200] = 600.0                                        # a tower ...
        z[200, 190:199] = 590.0                                    # ... a dip of 10 m between ...
        z[195:206, 175:190] = 620.0                                # ... and a face behind it
        write_cm(os.path.join(d, '540_5744.tif'), z, 540000, 5745000)
        self.tileindex.build(self.sources.BY_KEY['DE-NW'], 1)
        T = self.terrain.LidarTerrain(keys=['DE-NW'], log=lambda *a: None)
        tr = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True)
        corr = self.terrain.egm_correction('EPSG:4258+7837', *[[v] for v in tr.transform(540500, 5744500)])[0][0]
        lon, lat = tr.transform(540530.5, 5744499.5)               # 30 m east of the cone's top
        self.assertAlmostEqual(T.top(lon, lat, 15.0)[0], 500.0 - 0.2 * 15 + corr, delta=0.3)
        self.assertAlmostEqual(T.top(lon, lat, 15.0, ascent=60.0)[0], 500.0 + corr, delta=0.02)
        lon, lat = tr.transform(540200.5, 5744799.5)               # on the tower
        self.assertAlmostEqual(T.top(lon, lat, 15.0, ascent=60.0)[0], 620.0 + corr, delta=0.02)   # the face is within 15 m
        self.assertAlmostEqual(T.top(lon, lat, 5.0, ascent=60.0)[0], 600.0 + corr, delta=0.02)    # the tower, not the face

    def test_a_cell_reads_alike_through_any_patch(self):
        """ The correction to EGM2008 depends on the position alone, not on which window reads it. """
        T = self.terrain.LidarTerrain(keys=['DE-NW'], log=lambda *a: None)
        fid = int(T.src[0]['fid'][0])
        a = T.read_patch(0, fid, 300, 300, 66, 66).z[40, 40]           # cell (340, 340) ...
        b = T.read_patch(0, fid, 0, 0, 500, 500).z[340, 340]           # ... through a larger window
        c = T.read_patch(0, fid, 330, 320, 20, 30).z[10, 20]
        self.assertAlmostEqual(a, b, delta=0.001)
        self.assertAlmostEqual(a, c, delta=0.001)

    def test_frame_pools_to_10_m(self):
        T = self.terrain.LidarTerrain(keys=['DE-NW'], log=lambda *a: None)
        F = T.frame()
        from pyproj import Transformer
        lon, lat = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True).transform(530500.0, 5744500.0)
        gy, gx = F.cell_of(lon, lat)
        z = F.window(gy - 5, gx - 5, 11, 11)
        self.assertTrue(np.isfinite(z).all())
        # east is higher, by about 1 m a cell
        self.assertAlmostEqual(float(np.mean(np.diff(z, axis=1))), 1.0, delta=0.15)

    def test_coverage(self):
        T = self.terrain.LidarTerrain(keys=['DE-NW'], log=lambda *a: None)
        cov = self.terrain.Coverage(T, (9, 51, 10, 52), log=lambda *a: None)
        from pyproj import Transformer
        lon, lat = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True).transform(531000.0, 5744500.0)
        m = cov.tile(51, 9)
        N = cov.N
        self.assertTrue(m[int((52 - lat) * N), int((lon - 9) * N)])
        self.assertTrue(10 <= m.sum() <= 24)         # 2 km2 of 12" blocks (0.085 km2 each here): 23 at most


if __name__ == '__main__':
    unittest.main()
