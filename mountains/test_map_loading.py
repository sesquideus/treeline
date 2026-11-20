"""
The global map's loading by view (static/js/map.js): which tiles a view asks for, how what
comes back joins what is held, and which zoom a summit shows from in each mode. Run in the
real map.js through quickjs (mountains/test_js_runtime.py).
"""
import json

from django.test import SimpleTestCase

from mountains.test_js_runtime import call, evaluate

HALF = 20037508.342789244


def summit(pk, **properties):
    return {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [20, 49]},
            'properties': {'type': 'summit', 'pk': pk, **properties}}


class TileTests(SimpleTestCase):
    def tiles(self, extent, zoom, mode='prominence'):
        return call('mapTiles', extent, zoom, mode)

    def test_the_world_at_zoom_one_is_one_tile(self):
        tiles = self.tiles([-HALF, -HALF, HALF, HALF], 1)
        self.assertEqual([t['key'] for t in tiles], ['prominence/1/1/0/0'])
        minLon, minLat, maxLon, maxLat = tiles[0]['bbox']
        self.assertAlmostEqual(minLon, -180)
        self.assertAlmostEqual(maxLon, 180)
        self.assertAlmostEqual(maxLat, 85.0511, places=3)

    def test_a_view_and_a_tile_round_it(self):
        # zoom 8: 128 tiles of 512 px across the world; a view inside one asks for its 3×3
        size = 2 * HALF / 128
        x0, y0 = -HALF + 70 * size, HALF - 40 * size
        tiles = self.tiles([x0 + size * 0.2, y0 - size * 0.8, x0 + size * 0.6, y0 - size * 0.3], 8.6)
        self.assertEqual(sorted(t['key'] for t in tiles),
                         sorted(f'prominence/8/8/{x}/{y}' for x in (69, 70, 71) for y in (39, 40, 41)))
        self.assertTrue(all(t['z'] == 8 for t in tiles))

    def test_density_asks_for_another_zoom_on_the_same_tiles(self):
        dense = call('mapTiles', [0, 0, 1, 1], 8.6, 'prominence', 1)
        sparse = call('mapTiles', [0, 0, 1, 1], 8.6, 'prominence', -2)
        self.assertEqual({t['z'] for t in dense}, {9})
        self.assertEqual({t['z'] for t in sparse}, {6})
        self.assertEqual([t['bbox'] for t in dense], [t['bbox'] for t in sparse])
        self.assertNotEqual({t['key'] for t in dense}, {t['key'] for t in sparse})

    def test_the_mode_is_part_of_the_key(self):
        self.assertEqual(self.tiles([0, 0, 1, 1], 1, 'isolation')[0]['key'], 'isolation/1/1/0/0')

    def test_columns_wrap_round_the_antimeridian(self):
        size = 2 * HALF / 8
        tiles = self.tiles([HALF - size * 0.5, 0, HALF + size * 0.5, 1], 4)
        self.assertEqual(sorted({int(t['key'].split('/')[3]) for t in tiles}), [0, 1, 6, 7])
        for t in tiles:
            self.assertGreaterEqual(t['bbox'][0], -180 - 1e-9)
            self.assertLessEqual(t['bbox'][2], 180 + 1e-9)


class MergeTests(SimpleTestCase):
    def merge(self, held, payload):
        return evaluate(f'''(() => {{
            const store = {{ summits: {{ features: [] }}, cols: {{ features: [] }},
                            summitsByPk: {{}}, colsByPk: {{}} }};
            mergeMapFeatures(store, {held});
            const added = mergeMapFeatures(store, {payload});
            return {{ added: added.summits.map(f => f.properties.pk),
                      held: store.summits.features.map(f => f.properties.pk),
                      name: store.summitsByPk[1].properties.name }};
        }})()''')

    def test_only_what_is_new_is_added_and_what_is_held_is_kept(self):
        held = json.dumps({'summits': {'features': [summit(1, name='held')]}})
        payload = json.dumps({'summits': {'features': [summit(1, name='again'), summit(2)]},
                              'cols': {'features': []}})
        result = self.merge(held, payload)
        self.assertEqual(result['added'], [2])
        self.assertEqual(result['held'], [1, 2])
        self.assertEqual(result['name'], 'held')     # it may carry popup detail by now


class ModeZoomTests(SimpleTestCase):
    def vz(self, mode, **properties):
        return evaluate(f'''(() => {{
            const fs = [{json.dumps(summit(1, **properties))}];
            applyModeZooms(fs, {json.dumps(mode)});
            return fs[0].properties.vz;
        }})()''')

    def test_the_zoom_of_the_mode_on_show(self):
        zooms = {'none': 9, 'prominence': 4, 'isolation': 6, 'slope': 9, 'horizon': 9}
        self.assertEqual(self.vz('prominence', z=9, zooms=zooms), 4)
        self.assertEqual(self.vz('isolation', z=9, zooms=zooms), 6)
        self.assertEqual(self.vz('none', z=9, zooms=zooms), 9)

    def test_without_zooms_its_own(self):
        self.assertEqual(self.vz('prominence', z=7), 7)
