"""
Tests of tileindex.py (the per-tile statistics, the windows, the incremental build) and of the coverage
map's reading of the index, on small synthetic tiles in a temporary TREELINE_DATA.

    cd lidar && uv run --project .. --group lidar python -m unittest test_tileindex.py
"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest

import numpy as np
from rasterio.transform import from_origin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def write_cm(path, z, x0, y1, res=1.0, crs='EPSG:25832'):
    """ A tile as compact.py writes them: int32 cm, scale 0.01, INT32_MIN no data (NaN in z). """
    import rasterio
    q = np.where(np.isnan(z), np.iinfo(np.int32).min, np.round(z * 100)).astype(np.int32)
    with rasterio.open(path, 'w', driver='GTiff', width=z.shape[1], height=z.shape[0], count=1, dtype='int32',
                       crs=crs, transform=from_origin(x0, y1, res, res), nodata=np.iinfo(np.int32).min,
                       tiled=True, blockxsize=256, blockysize=256, compress='deflate') as d:
        d.write(q, 1)
        d.scales = (0.01,)


class Stats(unittest.TestCase):
    def setUp(self):
        import tileindex
        self.T = tileindex
        self.tr = from_origin(1000.0, 2000.0, 1.0, 1.0)

    def test_numbers(self):
        rng = np.random.default_rng(1)
        z = rng.normal(500, 20, (300, 400))
        z[:10] = np.nan
        z[50, 60] = 0.0
        s = self.T.stats(z, self.tr, 1.0)
        v = z[~np.isnan(z)]
        self.assertEqual(s['n_valid'], v.size)
        self.assertEqual(s['n_nodata'], 4000)
        self.assertEqual(s['n_zero'], 1)
        self.assertAlmostEqual(s['zmax'], v.max())
        self.assertAlmostEqual(s['zmin'], 0.0)
        self.assertAlmostEqual(s['zmean'], v.mean())
        self.assertAlmostEqual(s['p50'], np.percentile(v, 50))
        r, c = np.unravel_index(np.nanargmax(z), z.shape)
        self.assertEqual((s['xmax'], s['ymax']), (1000.5 + c, 1999.5 - r))      # cell centres
        self.assertEqual((s['xmin'], s['ymin']), (1060.5, 1949.5))

    def test_coarse(self):
        z = np.arange(64 * 96, dtype=np.float64).reshape(64, 96)
        z[0:2, 0:3] = np.nan                    # the first coarse cell entirely no data
        s = self.T.stats(z, self.tr, 1.0)
        row = {f'coarse_{k}': s[f'coarse_{k}'] for k in ('max', 'min', 'mean', 'n')}
        mx, mn, me, n = (self.T.coarse(row, k) for k in ('max', 'min', 'mean', 'n'))
        self.assertTrue(np.isnan(mx[0, 0]) and n[0, 0] == 0)
        self.assertEqual(n.sum(), 64 * 96 - 6)
        self.assertAlmostEqual(mx[31, 31], z[62:64, 93:96].max())
        self.assertAlmostEqual(mn[31, 31], z[62:64, 93:96].min())
        self.assertAlmostEqual(me[5, 7], z[10:12, 21:24].mean(), places=2)
        self.assertAlmostEqual(np.nanmax(mx), np.nanmax(z))

    def test_coarse_small_window(self):
        """ A window narrower than COARSE cells (an edge sliver of a big raster): empty coarse cells, no error. """
        z = np.full((5, 40), 7.0)
        s = self.T.stats(z, self.tr, 1.0)
        n = self.T.coarse({'coarse_n': s['coarse_n']}, 'n')
        self.assertEqual(n.sum(), 200)
        mx = self.T.coarse({'coarse_max': s['coarse_max']}, 'max')
        self.assertEqual(np.nanmax(mx), 7.0)

    def test_spikes(self):
        z = np.full((50, 50), 100.0)
        z[20, 20] = 130.0                       # up 30
        z[30, 30] = 90.0                        # down 10
        z[40, 40] = 103.0                       # within SPIKE
        z[10, 10] = np.nan
        z[10, 11] = 150.0                       # beside no data: compared with the neighbours that have data
        s = self.T.stats(z, self.tr, 1.0)
        self.assertEqual((s['spikes_up'], s['spikes_down']), (2, 1))
        self.assertAlmostEqual(s['spike'], 50.0)
        self.assertEqual((s['spike_x'], s['spike_y']), (1011.5, 1989.5))

    def test_peaks(self):
        y, x = np.mgrid[0:600, 0:600]
        z = 1000 - 0.01 * ((x - 300) ** 2 + (y - 300) ** 2) ** 0.5 * 10
        z += 15 * np.exp(-((x - 100) ** 2 + (y - 500) ** 2) / 200.0)    # a lower second top, 280 m away (cone 971.7 + 15)
        s = self.T.stats(z, self.tr, 1.0)
        p = json.loads(s['peaks'])
        self.assertEqual(p[0][:3], [1000.0, 1300.5, 1699.5])
        self.assertEqual(p[0][3], 0)
        self.assertTrue(any(abs(q[1] - 1100.5) < 2 and abs(q[2] - 1499.5) < 2 for q in p))
        for i in range(len(p)):                 # thinned: none within PEAK_R of another
            for j in range(i):
                self.assertGreater((p[i][1] - p[j][1]) ** 2 + (p[i][2] - p[j][2]) ** 2, self.T.PEAK_R ** 2)

    def test_all_nodata(self):
        s = self.T.stats(np.full((10, 10), np.nan), self.tr, 1.0)
        self.assertEqual((s['n_valid'], s['n_nodata']), (0, 100))
        self.assertNotIn('zmax', s)

    def test_windows(self):
        self.assertEqual(self.T.windows(2000, 2000), [(0, 0, 2000, 2000)])
        w = self.T.windows(5000, 3000)
        self.assertEqual(len(w), 3 * 2)
        self.assertEqual(sum(h * ww for _, _, h, ww in w), 5000 * 3000)


class Build(unittest.TestCase):
    """ The incremental build, and the coverage map from it, in a temporary data root. """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['TREELINE_DATA'] = self.tmp.name
        import data, sources, tileindex, coverage_map
        for m in (data, sources, tileindex, coverage_map):
            importlib.reload(m)
        self.S, self.T, self.C = sources, tileindex, coverage_map
        self.dir = os.path.join(self.tmp.name, 'intcm', 'DE', 'NW', 'tiles')
        os.makedirs(self.dir)
        self.src = sources.BY_KEY['DE-NW']
        rng = np.random.default_rng(2)
        for e in (530, 531):
            z = rng.normal(200, 5, (1000, 1000))
            if e == 531:
                z[:, 500:] = np.nan             # half a tile: the state's edge
            write_cm(os.path.join(self.dir, f'{e}_5744.tif'), z, e * 1000, 5745000)
        big = rng.normal(300, 5, (3000, 2600))
        write_cm(os.path.join(self.dir, '600_5800.tif'), big, 600000, 5803000)

    def tearDown(self):
        os.environ.pop('TREELINE_DATA')
        self.tmp.cleanup()

    def rows(self):
        con = sqlite3.connect(self.T.db_path('DE-NW'))
        return con.execute('SELECT files.path, rows.row_off, rows.col_off, rows.n_valid FROM rows '
                           'JOIN files ON files.id = rows.file_id ORDER BY 1, 2, 3').fetchall()

    def test_build_resume_change(self):
        self.T.build(self.src, 2)
        r = self.rows()
        self.assertEqual(len(r), 2 + 2 * 2)     # two tiles, the big one in 2 x 2 windows
        self.assertEqual([x[3] for x in r if x[0] == 'tiles/531_5744.tif'], [500000])
        self.assertEqual(sum(x[3] for x in r if x[0] == 'tiles/600_5800.tif'), 3000 * 2600)
        # nothing changed: nothing redone
        before = sqlite3.connect(self.T.db_path('DE-NW')).execute('SELECT max(id) FROM rows').fetchone()
        self.T.build(self.src, 2)
        self.assertEqual(sqlite3.connect(self.T.db_path('DE-NW')).execute('SELECT max(id) FROM rows').fetchone(), before)
        # a changed tile is redone, the others kept
        time.sleep(0.01)
        write_cm(os.path.join(self.dir, '530_5744.tif'), np.full((1000, 1000), 42.0), 530000, 5745000)
        self.T.build(self.src, 2)
        con = sqlite3.connect(self.T.db_path('DE-NW'))
        self.assertEqual(con.execute("SELECT zmax FROM rows JOIN files ON files.id = rows.file_id "
                                     "WHERE path = 'tiles/530_5744.tif'").fetchall(), [(42.0,)])
        self.assertEqual(len(self.rows()), 6)
        self.assertEqual(con.execute('SELECT count(*) FROM rows_ll').fetchone()[0], 6)     # no stale R*tree entries
        # the R*tree finds a tile by lon/lat
        from pyproj import Transformer
        lon, lat = Transformer.from_crs('EPSG:25832', 'EPSG:4326', always_xy=True).transform(530500, 5744500)
        hit = self.T.query('DE-NW', lon - 1e-4, lat - 1e-4, lon + 1e-4, lat + 1e-4)
        self.assertEqual({h['path'] for h in hit}, {'tiles/530_5744.tif'})

    def test_coverage_from_index(self):
        self.T.build(self.src, 2)
        T, H, W = self.C.grid()
        m, nf, nb = self.C.mask('DE-NW', T, H, W)
        self.assertEqual(nf, 3)
        km2 = m.sum() * self.C.RES ** 2 / 1e6
        # whole, half, 3 x 2.6 km; a map cell counts if any of it has data: up to perimeter x RES more, never less
        self.assertGreaterEqual(km2, 1 + 0.5 + 7.8)
        self.assertLessEqual(km2, 1 + 0.5 + 7.8 + (4 + 3 + 11.2) * self.C.RES / 1000)
