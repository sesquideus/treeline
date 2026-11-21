"""
Every altitude names its vertical datum, and is carried over into EGM2008.

The conversion tests need the PROJ grids (see CLAUDE.md); without them they are skipped,
loudly, rather than passing on PROJ's ballpark no-op.
"""
import importlib
import io

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from pyproj.transformer import TransformerGroup

from core.functions.heights import HeightConversionError, operation, to_egm2008
from core.models import VerticalDatum
from mountains.models import NamedPoint
from mountains.templatetags.tree import datum
from mountains.test_factories import make_col, make_country, make_point, make_summit

BPV = 'EPSG:4258+8357'
PL = 'EPSG:4258+9651'
GERLACH = (20.1336, 49.1640, 2654.4)
SNEZKA = (15.7398, 50.7360, 1603.0)
BABIA = (19.5295, 49.5731, 1725.0)
MONT_BLANC = (6.8652, 45.8326, 4808.0)


def grids_present():
    groups = [TransformerGroup(source, 'EPSG:4326+3855', always_xy=True) for source in (BPV, PL)]
    return all(group.best_available for group in groups)


class GridsMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not grids_present():
            raise cls.skipTest(cls, 'PROJ geoid grids missing: see CLAUDE.md, "Vertical datums"')


class ConversionTests(GridsMixin, SimpleTestCase):
    def test_egm2008_is_the_identity(self):
        self.assertEqual(to_egm2008(*GERLACH, 'EPSG:4326+3855'), GERLACH[2])

    def test_bpv_in_the_tatras_moves_by_centimetres(self):
        shift = to_egm2008(*GERLACH, BPV) - GERLACH[2]
        self.assertNotEqual(shift, 0)
        self.assertLess(abs(shift), 0.5)

    def test_each_country_uses_its_own_grid(self):
        self.assertIn('Baltic 1957 height (1)', operation(*GERLACH[:2], BPV))   # GKÚ
        self.assertIn('Baltic 1957 height (2)', operation(*SNEZKA[:2], BPV))    # ČÚZK
        self.assertIn('EVRF2007-PL', operation(*BABIA[:2], PL))

    def test_the_direct_route_agrees_with_the_one_through_the_ellipsoid(self):
        from pyproj import Transformer
        down = Transformer.from_crs(BPV, 'EPSG:4937', always_xy=True, allow_ballpark=False)
        ellipsoidal = down.transform(*GERLACH, errcheck=True)
        self.assertAlmostEqual(to_egm2008(*ellipsoidal, 'EPSG:4937'),
                               to_egm2008(*GERLACH, BPV), places=3)

    def test_ellipsoidal_heights_drop_by_the_geoid(self):
        self.assertAlmostEqual(to_egm2008(*GERLACH, 'EPSG:4937') - GERLACH[2], -42.66, places=1)

    def test_outside_every_grid_is_an_error_not_the_input(self):
        with self.assertRaises(HeightConversionError):
            to_egm2008(*MONT_BLANC, BPV)


class UnreachableDatumTests(SimpleTestCase):
    def test_a_datum_proj_cannot_reach_is_an_error_every_time(self):
        # TWVD 2001: PROJ has no operation to EGM2008 but the ballpark, which is refused.
        for _ in range(2):              # the second from the cache
            with self.assertRaises(HeightConversionError):
                to_egm2008(121.0, 23.47, 3952.0, 'EPSG:3824+8904')


TWVD2001 = ('+proj=longlat +ellps=GRS80 +towgs84=0,0,0 +geoidgrids=tw_isg_twgeoid18h.gtx +vunits=m '
            '+no_defs +type=crs')


class TaiwanTests(SimpleTestCase):
    """ TWVD 2001 through TWGEOID18h, installed by `install_isg_grid` (CLAUDE.md). """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from pathlib import Path
        from pyproj.datadir import get_user_data_dir
        if not (Path(get_user_data_dir()) / 'tw_isg_twgeoid18h.gtx').exists():
            raise cls.skipTest(cls, 'TWGEOID18h missing: see CLAUDE.md, "Vertical datums"')

    def test_yushan_rises_by_most_of_a_metre(self):
        self.assertAlmostEqual(to_egm2008(120.9573, 23.4699, 3947.46, TWVD2001) - 3947.46, 0.727, places=2)

    def test_kinmen_is_inside_the_grid(self):
        self.assertAlmostEqual(to_egm2008(118.40, 24.43, 0.0, TWVD2001), 0.686, places=2)

    def test_outside_the_grid_is_an_error(self):
        with self.assertRaises(HeightConversionError):
            to_egm2008(139.7, 35.7, 0.0, TWVD2001)


ISG = '\n'.join([
    'These data are freely available',
    'begin_of_head ================================================',
    'model name     : test',
    'data format    : grid',
    'data units     : meters',
    'data ordering  : N-to-S, W-to-E',
    'lat min        =   21°00\'00"',
    'lat max        =   21°01\'00"',
    'lon min        =  118°00\'00"',
    'lon max        =  118°00\'30"',
    'delta lat      =         30"',
    'delta lon      =         30"',
    'nrows          =           3',
    'ncols          =           2',
    'nodata         =  -9999.0000',
    'end_of_head ==================================================',
    ' 3.0 3.5',
    ' 2.0 -9999.0',
    ' 1.0 1.5', ''])


class IsgGridTests(SimpleTestCase):
    def test_angles(self):
        from core.management.commands.install_isg_grid import degrees
        self.assertAlmostEqual(degrees('118°00\'30"'), 118 + 30 / 3600)
        self.assertAlmostEqual(degrees('30"'), 30 / 3600)
        self.assertAlmostEqual(degrees('-5°30\'00"'), -5.5)
        self.assertEqual(degrees('21.25'), 21.25)

    def test_gtx_runs_south_to_north_from_the_south_west_node(self):
        import struct
        from core.management.commands.install_isg_grid import gtx, parse_isg
        b = gtx(*parse_isg(ISG))
        lat0, lon0, dlat, dlon, rows, cols = struct.unpack('>4d2i', b[:40])
        self.assertEqual((lat0, lon0, rows, cols), (21.0, 118.0, 3, 2))
        self.assertAlmostEqual(dlat, 30 / 3600)
        z = struct.unpack(f'>{rows * cols}f', b[40:])
        self.assertEqual(z[:2], (1.0, 1.5))
        self.assertAlmostEqual(z[3], -88.8888, places=3)


class DefaultDatumTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sk, cls.cz, cls.pl, cls.at = (make_country(code) for code in ('sk', 'cz', 'pl', 'at'))

    def point(self, *countries):
        point = make_point(None, 1000.0, 49.0, 20.0)
        point.countries.set(countries)
        return point

    def test_by_country(self):
        cases = [((self.sk,), 'Bpv'), ((self.cz,), 'Bpv'), ((self.pl,), 'PL-EVRF2007'),
                 ((self.pl, self.sk), 'Bpv'), ((self.cz, self.pl), 'Bpv'), ((self.at,), None)]
        for countries, expected in cases:
            with self.subTest(countries=[c.code for c in countries]):
                found = self.point(*countries).default_datum()
                self.assertEqual(found and found.code, expected)

    def test_a_countryless_key_col_follows_its_summit(self):
        col = make_col(None, 900.0, 49.0, 20.01)
        summit = make_summit('Top', 1000.0, 49.0, 20.0, key_col=col)
        summit.point.altitude_datum = VerticalDatum.objects.get(code='Bpv')
        summit.point.save()
        self.assertEqual(col.point.default_datum().code, 'Bpv')


class MigrationRuleTests(TestCase):
    """The frozen copy in migration 0041 makes the same choices as `default_datum()`."""

    def test_the_migration_assigns_like_default_datum(self):
        sk, pl, at = (make_country(code) for code in ('sk', 'pl', 'at'))
        points = {}
        for label, countries in (('sk', [sk]), ('pl', [pl]), ('plsk', [pl, sk]), ('at', [at])):
            points[label] = make_point(label, 1000.0, 49.0, 20.0)
            points[label].countries.set(countries)
        col = make_col(None, 900.0, 49.0, 20.01)
        make_summit('Top', 1000.0, 49.0, 20.0, key_col=col).point.countries.set([sk])

        from django.apps import apps
        importlib.import_module('mountains.migrations.0041_altitude_datum').assign_datums(apps, None)

        found = {label: NamedPoint.objects.get(pk=p.pk).altitude_datum for label, p in points.items()}
        self.assertEqual({k: v and v.code for k, v in found.items()},
                         {'sk': 'Bpv', 'pl': 'PL-EVRF2007', 'plsk': 'Bpv', 'at': None})
        self.assertEqual(NamedPoint.objects.get(pk=col.point.pk).altitude_datum.code, 'Bpv')


class SaveTests(GridsMixin, TestCase):
    def test_saving_computes_and_recomputes_the_egm2008_altitude(self):
        point = make_point('Gerlach', GERLACH[2], GERLACH[1], GERLACH[0])
        self.assertIsNone(point.altitude_egm2008)                      # no datum, no value
        point.altitude_datum = VerticalDatum.objects.get(code='Bpv')
        point.save()
        first = NamedPoint.objects.get(pk=point.pk).altitude_egm2008
        self.assertAlmostEqual(first, to_egm2008(*GERLACH, BPV), places=6)
        point.altitude = 2655.4
        point.save(update_fields=['altitude'])
        self.assertAlmostEqual(NamedPoint.objects.get(pk=point.pk).altitude_egm2008, first + 1, places=6)

    def test_a_save_not_touching_the_height_leaves_it_alone(self):
        point = make_point('Gerlach', GERLACH[2], GERLACH[1], GERLACH[0])
        NamedPoint.objects.filter(pk=point.pk).update(altitude_egm2008=1.0)
        point.name = 'Gerlachovský štít'
        point.save(update_fields=['name'])
        self.assertEqual(NamedPoint.objects.get(pk=point.pk).altitude_egm2008, 1.0)

    def test_a_failed_conversion_stores_nothing_rather_than_the_raw_altitude(self):
        point = make_point('Mont Blanc', MONT_BLANC[2], MONT_BLANC[1], MONT_BLANC[0])
        point.altitude_datum = VerticalDatum.objects.get(code='Bpv')
        with self.assertLogs('mountains.models.point', 'WARNING'):
            point.save()
        self.assertIsNone(NamedPoint.objects.get(pk=point.pk).altitude_egm2008)


class RecomputeCommandTests(GridsMixin, TestCase):
    def test_fills_every_point_with_a_datum_and_reports_the_shift(self):
        point = make_point('Gerlach', GERLACH[2], GERLACH[1], GERLACH[0])
        NamedPoint.objects.filter(pk=point.pk).update(
            altitude_datum=VerticalDatum.objects.get(code='Bpv'))
        out = io.StringIO()
        call_command('recompute_altitudes', stdout=out)
        self.assertIsNotNone(NamedPoint.objects.get(pk=point.pk).altitude_egm2008)
        self.assertIn('Bpv', out.getvalue())
        self.assertIn('1 points updated', out.getvalue())


class DatumFilterTests(TestCase):
    def test_unknown(self):
        self.assertIn('unknown', datum(make_point('X', 1000.0, 49.0, 20.0)))

    def test_known_with_the_egm2008_figure_when_it_differs(self):
        point = make_point('X', 1000.0, 49.0, 20.0)
        point.altitude_datum = VerticalDatum.objects.get(code='Bpv')
        point.altitude_egm2008 = 1000.2
        self.assertIn('>Bpv<', datum(point))
        self.assertIn('1000.2&nbsp;m EGM2008', datum(point))
        point.altitude_egm2008 = 1000.03
        self.assertNotIn('EGM2008', datum(point))

    def test_the_summit_page_shows_it(self):
        summit = make_summit('Top', 1000.0, 49.0, 20.0)
        html = self.client.get(summit.get_absolute_url()).content.decode()
        self.assertIn('class="datum unknown"', html)


class SummitAltitudesTests(TestCase):
    def test_the_title_has_only_the_original_and_the_list_below_has_both(self):
        summit = make_summit('Top', 1000.0, 49.0, 20.0)
        NamedPoint.objects.filter(pk=summit.point.pk).update(
            altitude_datum=VerticalDatum.objects.get(code='Bpv'), altitude_egm2008=1000.04)
        html = self.client.get(summit.get_absolute_url()).content.decode()
        title = html.split('<span class="mountain">')[1].split('</h1>')[0]
        self.assertIn('(1000.0 m)', title)
        self.assertNotIn('EGM2008', title)
        self.assertNotIn('Bpv', title)
        altitudes = html.split('<ul class="altitudes">')[1].split('</ul>')[0]
        self.assertIn('1000.0 m', altitudes)
        self.assertIn('Bpv', altitudes)
        self.assertIn('1000.04 m EGM2008', altitudes)
