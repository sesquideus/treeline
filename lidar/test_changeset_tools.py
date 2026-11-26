"""
Tests of burn_match.py (the guard, the database's summits) and catchments_changeset.py (names in
the local script, altitude_review, regions reassigned, the review file), on small synthetic runs.

    cd lidar && uv run --project .. --group lidar python -m unittest test_changeset_tools.py
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import burn_match  # noqa: E402
import catchments_changeset as cc  # noqa: E402


def top(lat, lon, h, prom, **kw):
    return dict(top=[lat, lon], h=h, prom=prom, border=False, **kw)


def peak(qid, lat, lon, elev, label=None, precision=0.0001, prom=None):
    return dict(qid=qid, label=label, lat=lat, lon=lon, precision=precision, elev=elev, prom=prom)


class Guard(unittest.TestCase):
    """ burn_match.pair: the model may read at most `below` m under the stated height, `above` over. """

    def test_default_guard_takes_a_summit_the_model_reads_low(self):
        pairs, *_ = burn_match.pair([top(48.0, 24.0, 1900.0, 300)], [peak('Q1', 48.0, 24.0, 2000.0)])
        self.assertEqual([(j, i) for j, i, _ in pairs], [(0, 0)])

    def test_tighter_guard_refuses_it(self):
        pairs, *_ = burn_match.pair([top(48.0, 24.0, 1900.0, 300)], [peak('Q1', 48.0, 24.0, 2000.0)],
                                    below=40, above=30)
        self.assertEqual(pairs, [])

    def test_tighter_guard_keeps_a_sharp_summit_read_up_to_40_m_low(self):
        pairs, *_ = burn_match.pair([top(48.0, 24.0, 1962.0, 300)], [peak('Q1', 48.0, 24.0, 2000.0)],
                                    below=40, above=30)
        self.assertEqual(len(pairs), 1)

    def test_model_above_by_more_than_above_is_refused(self):
        pairs, *_ = burn_match.pair([top(48.0, 24.0, 2031.0, 300)], [peak('Q1', 48.0, 24.0, 2000.0)],
                                    below=40, above=30)
        self.assertEqual(pairs, [])

    def test_near_known(self):
        peaks = [peak('Q1', 48.0, 24.0, 2000.0), peak('Q2', 48.1, 24.0, 2000.0)]
        known = [dict(pk=1, name='Говерла', lat=48.001, lon=24.0, alt=2061.0)]   # ~110 m from Q1
        self.assertEqual(burn_match.near_known(peaks, known, 0.3), {0})


class BurnMatchMain(unittest.TestCase):
    """ burn_match.main with a stand-in for the model: --check-position and --known. """

    def run_main(self, tops, peaks, known=None, model=lambda lat, lon: None, **kw):
        d = tempfile.mkdtemp()
        paths = {}
        for name, data in (('run', dict(summits=tops)), ('peaks', peaks), ('known', known)):
            if data is not None:
                paths[name] = os.path.join(d, name + '.json')
                json.dump(data, open(paths[name], 'w'))
        out = os.path.join(d, 'burns.json')
        fake = mock.MagicMock()
        fake.ARC = 3600
        import numpy as np

        def tile(path):
            lat0, lon0 = path                              # (lat, lon) from model_tile below
            t = np.zeros((3601, 3601), np.float32)
            for (la, lo), h in model.items():
                if int(la // 1) == lat0 and int(lo // 1) == lon0:
                    t[int(round((lat0 + 1 - la) * 3600)), int(round((lo - lon0) * 3600))] = h
            return t
        fake.model_tile = lambda la, lo: (la, lo)
        fake.read_model = tile                             # the scaled reader burn_match uses
        ras = mock.MagicMock()
        ras.open = lambda p: mock.MagicMock(read=lambda b: tile(p))
        with mock.patch.dict(sys.modules, {'catchments': fake, 'rasterio': ras}), mock.patch('builtins.print'):
            burn_match.main(paths['run'], paths['peaks'], out, known_path=paths.get('known'), **kw)
        return json.load(open(out))

    def test_precise_coordinate_off_the_summit_burns_at_the_top(self):
        # Wikidata 1.4 km from the summit, on a slope the model reads 1365 m (Berlebashka)
        tops = [top(47.95, 24.32, 1727.4, 175)]
        peaks = [peak('Q1', 47.9639, 24.3038, 1733.9, 'Berlebashka')]
        model = {(47.95, 24.32): 1727.4, (47.9639, 24.3038): 1365.0}
        loose = self.run_main(tops, peaks, model=model, below=40, above=30)
        self.assertEqual(loose['burns'][0]['at'], [47.9639, 24.3038])
        checked = self.run_main(tops, peaks, model=model, below=40, above=30, check_position=True)
        self.assertEqual(checked['burns'][0]['at'], [47.95, 24.32])
        self.assertIn("coordinate is off", checked['burns'][0]['placed'])

    def test_precise_coordinate_on_the_slope_burns_at_the_top(self):
        # 100 m down the slope from the model's top, 38 m below it there (Khomiak, Ai-Nikola)
        tops = [top(48.3675, 24.4969, 1538.6, 300)]
        peaks = [peak('Q1', 48.3675, 24.4983, 1542.0, 'Khomiak')]
        model = {(48.3675, 24.4969): 1538.6, (48.3675, 24.4983): 1500.0}
        checked = self.run_main(tops, peaks, model=model, below=40, above=30, check_position=True)
        self.assertEqual(checked['burns'][0]['at'], [48.3675, 24.4969])
        self.assertIn('on its slope', checked['burns'][0]['placed'])
        # on the top itself (the model one cell lower there): at Wikidata's coordinate
        model = {(48.3675, 24.4969): 1538.6, (48.3675, 24.49718): 1536.0}
        peaks = [peak('Q1', 48.3675, 24.49718, 1542.0, 'Khomiak')]
        checked = self.run_main(tops, peaks, model=model, below=40, above=30, check_position=True)
        self.assertEqual(checked['burns'][0]['at'], [48.3675, 24.49718])

    def test_unpaired_precise_coordinate_on_the_slope_burns_at_the_highest_cell(self):
        # no top of its own in the run (Drahobrat): raised at the model's highest cell within 150 m
        peaks = [peak('Q1', 48.2322, 24.2319, 1763.3, 'Drahobrat')]
        model = {(48.2322, 24.2319): 1725.0, (48.2322, 24.2330): 1745.0}
        res = self.run_main([], peaks, model=model, below=40, above=30, check_position=True)
        self.assertEqual(len(res['burns']), 1)
        self.assertAlmostEqual(res['burns'][0]['at'][1], 24.2330, places=3)
        self.assertIn('on its slope', res['burns'][0]['placed'])
        loose = self.run_main([], peaks, model=model, below=40, above=30)
        self.assertEqual(loose['burns'][0]['at'], [48.2322, 24.2319])

    def test_known_summits_burned_and_wikidata_near_them_deferred(self):
        tops = [top(48.16, 24.50, 2058.0, 700)]
        peaks = [peak('Q1', 48.1601, 24.5001, 2061.0, 'Hoverla')]
        known = [dict(pk=38, name='Говерла', lat=48.16, lon=24.50, alt=2061.0)]
        res = self.run_main(tops, peaks, known=known, model={(48.16, 24.50): 2058.0}, below=40, above=30)
        self.assertEqual([b.get('db') for b in res['burns']], [38])
        self.assertEqual(res['burns'][0]['source'], 'database')
        self.assertEqual([d['qid'] for d in res['deferred']], ['Q1'])

    def test_known_summit_the_model_disagrees_with_is_not_burned(self):
        known = [dict(pk=1, name='X', lat=48.16, lon=24.50, alt=2161.0)]
        res = self.run_main([top(48.16, 24.50, 2058.0, 700)], [], known=known,
                            model={(48.16, 24.50): 2058.0}, below=40, above=30)
        self.assertEqual(res['burns'], [])

    def test_known_below_lets_a_sharp_summit_stand_far_above_the_model(self):
        # the Matterhorn: 4478 m, the 30 m model 4328 m; Wikidata alone would still be refused
        known = [dict(pk=510, name='Matterhorn', lat=45.9764, lon=7.6586, alt=4478.0)]
        model = {(45.9764, 7.6586): 4328.0}
        tops = [top(45.9764, 7.6586, 4328.0, 1000)]
        res = self.run_main(tops, [], known=known, model=model, below=40, above=30, known_below=250)
        self.assertEqual([(b.get('db'), b['elev']) for b in res['burns']], [(510, 4478.0)])
        res = self.run_main(tops, [peak('Q1', 45.9764, 7.6586, 4478.0, 'Matterhorn')], model=model,
                            below=40, above=30, known_below=250)
        self.assertEqual(res['burns'], [])


class OsmPeaks(unittest.TestCase):
    """ burn_match: OSM's peaks as summits to burn, Wikidata's as a cross-check and a fallback. """

    def test_osm_ele(self):
        self.assertEqual(burn_match.osm_ele('2061'), 2061.0)
        self.assertEqual(burn_match.osm_ele('2061 m'), 2061.0)
        self.assertEqual(burn_match.osm_ele('2061,5'), 2061.5)
        self.assertIsNone(burn_match.osm_ele('~2000'))
        self.assertIsNone(burn_match.osm_ele(None))

    def test_load_osm_peaks(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, 'osm.json')
        json.dump([dict(id=1, lat=48.16, lon=24.5, kind='peak', name='Говерла', other_names={}, ele='2061'),
                   dict(id=2, lat=48.17, lon=24.5, kind='saddle', name='Перевал', other_names={}, ele='1800'),
                   dict(id=3, lat=48.18, lon=24.5, kind='peak', name='Без висоти', other_names={}, ele=None)],
                  open(path, 'w'))
        peaks = burn_match.load_peaks(path)
        self.assertEqual([(p['qid'], p['elev'], p['source']) for p in peaks], [('osm:1', 2061.0, 'OpenStreetMap')])

    def test_combine(self):
        osm = [dict(qid='osm:1', label='A', lat=48.0, lon=24.0, precision=1e-5, elev=1500.0, prom=None,
                    source='OpenStreetMap')]
        wd = [peak('Q1', 48.001, 24.0, 1510.0), peak('Q2', 48.2, 24.0, 900.0)]
        out = burn_match.combine(osm, wd, confirmed_below=100)
        self.assertEqual([p['qid'] for p in out], ['osm:1', 'Q2'])      # Q1 checks osm:1, Q2 stands alone
        self.assertEqual(out[0]['below'], 100)
        self.assertEqual(out[0]['checked'], [dict(qid='Q1', elev=1510.0)])
        fresh = dict(qid='osm:1', label='A', lat=48.0, lon=24.0, precision=1e-5, elev=1500.0, prom=None)
        far = burn_match.combine([fresh], [peak('Q1', 48.001, 24.0, 1560.0)], confirmed_below=100)
        self.assertNotIn('below', far[0])                                 # they disagree: the usual guard

    def test_confirmed_guard_wider(self):
        tops = [top(48.0, 24.0, 1420.0, 300)]                            # 80 m low
        osm = [dict(qid='osm:1', label='A', lat=48.0, lon=24.0, precision=1e-5, elev=1500.0, prom=None,
                    source='OpenStreetMap')]
        self.assertEqual(burn_match.pair(tops, osm, below=40, above=30)[0], [])
        self.assertEqual(len(burn_match.pair(tops, [dict(osm[0], below=100)], below=40, above=30)[0]), 1)


class GedtmFetch(unittest.TestCase):
    """ gedtm30.read_window: retried with backoff, then read in smaller windows. """

    def fake_open(self, fail_big, fail_first=0):
        import numpy as np
        import rasterio
        calls = dict(n=0)

        class DS:
            nodata = 3.4e38

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, band, window=None, **kw):
                calls['n'] += 1
                if calls['n'] <= fail_first or (fail_big and window.width > 100):
                    raise rasterio.errors.RasterioIOError('Read failed')
                z = np.full((int(window.height), int(window.width)), 100.0, np.float32)
                z[0, 0] = 3.4e38
                return z
        return mock.patch('rasterio.open', lambda *a, **k: DS()), calls

    def test_retried(self):
        import gedtm30
        patch, calls = self.fake_open(fail_big=False, fail_first=2)
        with patch, mock.patch('time.sleep'), mock.patch('builtins.print'):
            z = gedtm30.read_window(0, 0, 200, 200)
        self.assertEqual(calls['n'], 3)
        self.assertTrue(np.isnan(z[0, 0]) and z[1, 1] == 100.0)

    def test_split_when_the_whole_keeps_failing(self):
        import gedtm30
        patch, calls = self.fake_open(fail_big=True)
        with patch, mock.patch('time.sleep'), mock.patch('builtins.print'):
            z = gedtm30.read_window(0, 0, 200, 200, tries=2, split=4)
        self.assertEqual(z.shape, (200, 200))
        self.assertEqual(calls['n'], 2 + 16)
        self.assertEqual(int(np.isnan(z).sum()), 16)            # each piece's first cell was no data


class SplitClashes(unittest.TestCase):
    """ split_clashes.split: the clean part never touches what the database holds; clashes listed. """

    def setUp(self):
        import split_clashes
        self.split = split_clashes.split
        self.run = dict(summits=[
            top(45.0, 25.0, 2000.0, 500, col=[44.9, 25.0], col_h=1500.0),      # the database's A
            top(45.1, 25.0, 1700.0, 300, col=[45.05, 25.0], col_h=1400.0, parent=[45.0, 25.0]),
            top(45.2, 25.0, 1600.0, 200, col=[45.15, 25.0], col_h=1400.0, parent=[45.1, 25.0]),
            top(45.3, 25.0, 1500.0, 900, col=[45.25, 25.0], col_h=600.0),
        ])
        self.db = [dict(pk=1, name='A', lat=45.0001, lon=25.0, alt=1960.0, source='Wikidata', review=None, countries=['ro'],
                        col=dict(lat=44.9, lon=25.0, alt=1500.0), parent=None),
                   dict(pk=2, name='Vânătoarea', lat=45.33, lon=25.0, alt=1500.0, source='Wikidata', review=None,
                        countries=['ro'], col=dict(lat=45.25, lon=25.0, alt=601.0), parent=None)]
        self.cs = dict(sources={}, summits=[
            dict(key='n1', lat=45.1, lon=25.0, name='B', names=[], altitude=1700.0, prominence=300.0,
                 parent=dict(at=[45.0001, 25.0]), notes=['x']),
            dict(key='n2', lat=45.2, lon=25.0, name='C', names=[], altitude=1600.0, prominence=200.0,
                 parent=dict(new='n1'), notes=['x']),
            dict(key='n3', lat=45.3, lon=25.0, name='Vânătarea', names=[], altitude=1500.0, prominence=900.0,
                 parent=None, notes=['x'])],
            line_parents=[dict(summit_at=[45.2, 25.0], parent_at=[45.3, 25.0], cutoff=30)])

    def test_split(self):
        out, clashes = self.split(self.run, self.cs, self.db, [], 'ro')
        kinds = {c['kind'] for c in clashes}
        self.assertIn('differs', kinds)                                   # A: 1960 against the run's 2000
        differ = [c for c in clashes if c['kind'] == 'differs'][0]
        self.assertTrue(any('height' in w for w in differ['why']))
        dropped = [c['key'] for c in clashes if c['kind'] == 'new near a database summit']
        self.assertEqual(dropped, ['n3'])                                 # nearly the name of #2, 3.3 km off
        self.assertEqual([s['key'] for s in out['summits']], ['n1', 'n2'])
        self.assertEqual(out['line_parents'], [])                         # to the dropped summit

    def test_dropped_parent_hands_down(self):
        cs = dict(self.cs)
        cs['summits'] = [dict(self.cs['summits'][0], lat=45.0005), self.cs['summits'][1]]  # n1 on A's top
        out, clashes = self.split(self.run, cs, self.db, [], 'ro')
        self.assertEqual([s['key'] for s in out['summits']], ['n2'])
        self.assertEqual(out['summits'][0]['parent'], dict(at=[45.0001, 25.0]))


class Helpers(unittest.TestCase):
    def test_local_form(self):
        self.assertEqual(cc.local_form('Гора Стіг'), 'Стіг')
        self.assertEqual(cc.local_form('Стіг (гора)'), 'Стіг')
        self.assertEqual(cc.local_form('Гостра Гора'), 'Гостра Гора')
        self.assertEqual(cc.local_form('Кам’янка'), 'Кам’янка')
        self.assertIsNone(cc.local_form('Hora Stih'))          # tagged uk in GeoNames, but Latin
        self.assertIsNone(cc.local_form(None))

    def test_local_form_latin(self):
        self.assertEqual(cc.local_form('Vârful Omu', cc.LATIN), 'Omu')
        self.assertEqual(cc.local_form('Pietrosul Rodnei', cc.LATIN), 'Pietrosul Rodnei')
        self.assertIsNone(cc.local_form('Говерла', cc.LATIN))

    def test_review_height(self):
        self.assertEqual(cc.review_height(2050.0, 2061.0), ('O', 11.0))
        self.assertEqual(cc.review_height(1185.9, 1223.5)[0], 'W')
        self.assertEqual(cc.review_height(873.0, 833.0)[0], 'W')
        self.assertEqual(cc.review_height(1000.0, None), (None, None))

    def test_review_many(self):
        self.assertIsNone(cc.review_many({}, 1500.0, 'GEDTM30'))
        self.assertEqual(cc.review_many({'OSM': 1510.0}, 1500.0, 'GEDTM30')[0], 'O')
        r = cc.review_many({'OSM': 1510.0, 'Wikidata': 1540.0}, 1505.0, 'GEDTM30')
        self.assertEqual(r[0], 'W')
        self.assertIn('OSM 1510 m', r[1])
        self.assertIn('Wikidata 1540 m', r[1])
        self.assertIn('GEDTM30 1505 m', r[1])

    def test_misplaced(self):
        p = peak('Q1', 48.0, 24.0, 1500.0)
        far = [top(48.0, 24.0, 1200.0, 100), top(48.015, 24.0, 1495.0, 300)]   # 1.7 km north
        self.assertIn('1.7 km away', cc.misplaced(p, far)[0])
        self.assertIsNone(cc.misplaced(p, [top(48.001, 24.0, 1490.0, 300)]))
        osm = dict(lat=48.01, lon=24.0, tags=dict(ele='1500'))
        self.assertIn('OSM has this item', cc.misplaced(p, [top(48.0, 24.0, 1490.0, 300)], osm)[0])


def square(lat0, lon0, lat1, lon1):
    return dict(type='Polygon', coordinates=[[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]])


class ChangesetMain(unittest.TestCase):
    """ catchments_changeset.main on a run of a few tops in a made-up Ukraine and a made-up Crimea. """

    def setUp(self):
        self.d = tempfile.mkdtemp()
        j = lambda name, data: (json.dump(data, open(os.path.join(self.d, name), 'w'), ensure_ascii=False),
                                os.path.join(self.d, name))[1]
        tops = [
            # burned from Wikidata, agreeing: O
            top(48.00, 24.00, 1600.0, 400, col=[47.95, 24.0], col_h=1200.0,
                height_from=dict(source='Wikidata', qid='Q1', label='Mount One', how='burned',
                                 placed='wikidata', model=1590.0)),
            # named by a Wikidata item 35 m off the model: W
            top(48.10, 24.00, 1300.0, 200, col=[48.05, 24.0], col_h=1100.0, parent=[48.00, 24.00]),
            # a summit the database has
            top(48.20, 24.00, 1700.0, 500, col=[48.15, 24.0], col_h=1200.0),
            # nothing to compare, its parent beyond the tops kept (the database's): none
            top(48.30, 24.00, 900.0, 50, col=[48.29, 24.0], col_h=850.0, parent=[48.40, 26.50]),
            # Crimea, which the admin-0 map gives "Russia"
            top(44.60, 34.20, 1500.0, 300, col=[44.65, 34.0], col_h=1200.0),
            # the same name as a more prominent top 1 km away: left unnamed
            top(48.009, 24.00, 1560.0, 40, col=[48.005, 24.0], col_h=1520.0, parent=[48.00, 24.00]),
            # beyond the kept area
            top(48.40, 26.50, 1000.0, 600, col=[48.40, 26.4], col_h=400.0),
        ]
        self.run = j('run.json', dict(stats=dict(line_cutoff=30.0), summits=tops))
        self.known = j('known.json', [
            dict(pk=7, name='Говерла', lat=48.2001, lon=24.0, alt=1700.0, prom=500, countries=['ua'],
                 line_source='FABDEM V1-2'),
            dict(pk=8, name='Beyond', lat=48.40, lon=26.50, alt=1000.0, prom=600, countries=['ua'])])
        self.osm = j('osm.json', dict(elements=[]))
        self.admin0 = j('admin0.json', dict(type='FeatureCollection', features=[
            dict(type='Feature', geometry=square(46, 22, 53, 30),
                 properties=dict(ISO_A2_EH='UA', ADM0_A3='UKR')),
            dict(type='Feature', geometry=square(43, 32, 46, 37),
                 properties=dict(ISO_A2_EH='RU', ADM0_A3='RUS'))]))
        self.admin1 = j('admin1.json', dict(type='FeatureCollection', features=[
            dict(type='Feature', geometry=square(44, 33, 45.5, 35),
                 properties=dict(iso_3166_2='UA-43', iso_a2='RU', adm0_a3='RUS'))]))
        self.peaks = j('peaks.json', [
            peak('Q1', 48.0, 24.0, 1600.0, 'Mount One'),
            peak('Q2', 48.1005, 24.0, 1335.0, 'Two'),
            peak('Q3', 48.2, 24.0, 1700.0, 'Hoverla'),
            peak('Q4', 44.6, 34.2, 1505.0, None),
            peak('Q5', 48.0092, 24.0, 1550.0, None)])
        self.labels = j('labels.json', {'Q1': {'uk': 'Гора Перша', 'en': 'Mount One'}, 'Q2': {'uk': 'Друга'},
                                         'Q3': {'uk': 'Говерла'}, 'Q4': {'uk': 'Роман-Кош'},
                                         'Q5': {'uk': 'Перша (гора)'}})
        self.out = os.path.join(self.d, 'out.json')
        self.review = os.path.join(self.d, 'review.json')

    def make(self, *extra):
        argv = ['catchments_changeset.py', self.run, self.known, self.osm, self.admin0, self.out,
                '--countries', 'ua', '--exclude-box', '48,49,26,30', '--min-prominence', '30',
                '--source', 'FABDEM V1-2', '--wikidata', self.peaks, '--match', '0.3,0,0',
                '--borders', os.path.join(self.d, 'no-borders')] + list(extra)   # Natural Earth alone
        with mock.patch.object(sys, 'argv', argv), mock.patch('builtins.print'):
            cc.main()
        return {(s['lat'], s['lon']): s for s in json.load(open(self.out))['summits']}

    def make_ua(self):
        return self.make('--local-lang', 'uk', '--labels', self.labels, '--review-heights', '--parents-known',
                         '--keep-lines', '--assign', f'{self.admin1}:UA-43:ua', '--review', self.review)

    def test_without_the_options_as_before(self):
        s = self.make()
        self.assertEqual(s[(48.0, 24.0)]['name'], 'Mount One')
        self.assertNotIn('altitude_review', s[(48.0, 24.0)])
        self.assertNotIn((44.6, 34.2), s)                      # Crimea is Russia's on the map
        self.assertIsNone(s[(48.3, 24.0)]['parent'])

    def test_cyrillic_names(self):
        s = self.make_ua()
        one = s[(48.0, 24.0)]
        self.assertEqual(one['name'], 'Перша')
        self.assertEqual(one['names'][0], dict(lang='uk', name='Перша', local=True, source='Wikidata'))
        self.assertIn(dict(lang='en', name='Mount One', local=False, source='Wikidata'), one['names'])
        self.assertEqual(s[(48.1, 24.0)]['name'], 'Друга')
        self.assertIsNone(s[(48.3, 24.0)]['name'])             # unnamed stays unnamed
        self.assertNotIn((48.2, 24.0), s)                       # the database's summit, matched

    def geonames(self, langs='uk,ru-UA'):
        g = os.path.join(self.d, 'geonames')
        os.makedirs(g, exist_ok=True)
        for name, data in (('summits.json', []), ('summit-alternates.json', {}),
                           ('languages-wikidata.json', dict(results=dict(bindings=[]))),
                           ('treeline-languages.json', [dict(code='uk', english_name='Ukrainian'),
                                                        dict(code='ru', english_name='Russian'),
                                                        dict(code='en', english_name='English')])):
            json.dump(data, open(os.path.join(g, name), 'w'))
        open(os.path.join(g, 'country-languages.tsv'), 'w').write(f'ua\t{langs}\nru\tru\n')
        open(os.path.join(g, 'countryInfo.txt'), 'w').write('#\n')
        return g

    def test_auto_takes_the_countrys_languages_in_order(self):
        labels = json.load(open(self.labels))
        labels['Q2'] = {'ru': 'Вторая', 'en': 'Two'}             # none in the first language
        json.dump(labels, open(self.labels, 'w'), ensure_ascii=False)
        s = self.make('--local-lang', 'auto', '--labels', self.labels, '--geonames', self.geonames())
        self.assertEqual(s[(48.0, 24.0)]['name'], 'Перша')
        self.assertEqual(s[(48.0, 24.0)]['names'][0]['lang'], 'uk')
        self.assertEqual(s[(48.1, 24.0)]['name'], 'Вторая')
        self.assertEqual(s[(48.1, 24.0)]['names'][0], dict(lang='ru', name='Вторая', local=True, source='Wikidata'))

    def test_script_of(self):
        self.assertIs(cc.script_of('el'), cc.GREEK)
        self.assertIs(cc.script_of('gr'), cc.GREEK)               # TreeLine's code for Greek
        self.assertIs(cc.script_of('sr'), cc.CYRILLIC)
        self.assertIs(cc.script_of('it'), cc.LATIN)
        self.assertEqual(cc.local_form('Όλυμπος', cc.GREEK), 'Όλυμπος')
        self.assertIsNone(cc.local_form('Olympos', cc.GREEK))

    def test_repeated_name_near_left_unnamed(self):
        s = self.make_ua()
        self.assertIsNone(s[(48.009, 24.0)]['name'])
        rv = json.load(open(self.review))
        self.assertEqual([d['name'] for d in rv['unnamed_repeated_name']], ['Перша'])

    def test_altitude_review(self):
        s = self.make_ua()
        self.assertEqual(s[(48.0, 24.0)]['altitude_review'], 'O')
        two = s[(48.1, 24.0)]
        self.assertEqual(two['altitude_review'], 'W')
        self.assertTrue(any('1335 m' in n and '1300 m' in n and '35 m' in n for n in two['notes']))
        self.assertNotIn('altitude_review', s[(48.3, 24.0)])
        rv = json.load(open(self.review))
        self.assertEqual([w['qid'] for w in rv['warnings']], ['Q2'])

    def test_crimea_assigned_to_ukraine(self):
        s = self.make_ua()
        self.assertEqual(s[(44.6, 34.2)]['countries'], ['ua'])
        self.assertEqual(s[(44.6, 34.2)]['name'], 'Роман-Кош')

    def test_parent_beyond_is_the_database_summit(self):
        s = self.make_ua()
        self.assertEqual(s[(48.3, 24.0)]['parent'], dict(at=[48.4, 26.5]))

    def osm_peaks(self):
        path = os.path.join(self.d, 'osm-peaks.json')
        json.dump([dict(id=11, lat=48.0003, lon=24.0, kind='peak', name='Перший верх', ele='1600',
                        other_names={'uk': 'Перший верх', 'en': 'First Top', 'xyz': 'nope'}),
                   dict(id=12, lat=48.1001, lon=24.0, kind='peak', name='Друга', ele='1302', other_names={}),
                   dict(id=13, lat=48.2002, lon=24.0, kind='peak', name='Говерла', ele='1700', other_names={})],
                  open(path, 'w'), ensure_ascii=False)
        return path

    def test_osm_names_first(self):
        s = self.make('--local-lang', 'uk', '--labels', self.labels, '--review-heights', '--osm-peaks', self.osm_peaks())
        one = s[(48.0, 24.0)]
        self.assertEqual(one['name'], 'Перший верх')
        self.assertIn(dict(lang='uk', name='Перший верх', local=True, source='OpenStreetMap'), one['names'])
        self.assertIn(dict(lang='en', name='First Top', local=False, source='OpenStreetMap'), one['names'])
        self.assertNotIn('xyz', [n['lang'] for n in one['names']])
        two = s[(48.1, 24.0)]
        self.assertEqual(two['name'], 'Друга')
        # no name:uk, but Cyrillic: also the uk name
        self.assertEqual(two['names'][0], dict(lang='uk', name='Друга', local=True, source='OpenStreetMap'))

    def test_osm_review(self):
        s = self.make('--local-lang', 'uk', '--labels', self.labels, '--review-heights', '--osm-peaks', self.osm_peaks())
        # OSM 1600, Wikidata 1600, the model 1590 before the burn: O
        self.assertEqual(s[(48.0, 24.0)]['altitude_review'], 'O')
        # OSM 1302, Wikidata 1335, the model 1300: W, all three in the note
        two = s[(48.1, 24.0)]
        self.assertEqual(two['altitude_review'], 'W')
        self.assertTrue(any('OpenStreetMap node 12 1302 m' in n and 'Wikidata Q2 1335 m' in n for n in two['notes']))

    def test_no_known_lines(self):
        self.make('--no-known-lines')
        self.assertNotIn([48.2001, 24.0], [lp['summit_at'] for lp in json.load(open(self.out))['line_parents']])

    def test_known_line_parent_kept(self):
        self.make_ua()
        lines = json.load(open(self.out))['line_parents']
        self.assertNotIn([48.2001, 24.0], [lp['summit_at'] for lp in lines])


if __name__ == '__main__':
    unittest.main()
