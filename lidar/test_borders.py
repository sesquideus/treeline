"""Countries from OSM outlines: the line within TOL is both sides, Natural Earth only where no outline reaches."""
import json
import os
import sys
import tempfile
import unittest

from shapely.geometry import box, mapping

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from borders import Borders  # noqa: E402


def outline(folder, code, geom):
    json.dump(dict(type='FeatureCollection', features=[dict(type='Feature', properties=dict(code=code),
                                                            geometry=mapping(geom))]),
              open(os.path.join(folder, code.lower() + '.geojson'), 'w'))


class BordersTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        outline(self.dir, 'UA', box(22.0, 48.0, 24.0, 50.0))        # its western border at 22° E
        self.b = Borders(self.dir, tol=100)
        self.ne = lambda exclude: {'pl', 'ua'} - exclude            # the coarse outline: both, padded

    def test_well_inside(self):
        self.assertEqual(self.b.decide(49.0, 22.05, self.ne), {'ua'})        # 3.6 km in: not Poland

    def test_outside_takes_the_neighbour_not_the_outlined(self):
        self.assertEqual(self.b.decide(49.0, 21.98, self.ne), {'pl'})        # 1.5 km out: not Ukraine

    def test_on_the_line_both(self):
        self.assertEqual(self.b.decide(49.0, 22.0005, self.ne), {'pl', 'ua'})   # 37 m from it

    def test_beyond_every_outline_falls_back(self):
        self.assertEqual(self.b.decide(45.0, 10.0, lambda exclude: {'it'}), {'it'})
        self.assertEqual(self.b.coarse, 1)

    def test_two_outlines_share_the_line(self):
        outline(self.dir, 'PL', box(20.0, 48.0, 22.0, 50.0))
        b = Borders(self.dir, tol=100)
        self.assertEqual(b.decide(49.0, 21.95, lambda exclude: {'ua'} - exclude), {'pl'})      # an outline beats NE
        self.assertEqual(b.decide(49.0, 22.0003, lambda exclude: set()), {'pl', 'ua'})


if __name__ == '__main__':
    unittest.main()
