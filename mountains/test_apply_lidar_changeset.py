"""
`apply_lidar_changeset`: the P100s and names the lidar comparison found, applied to a
database. It is run here and then on the server, so it must be safe to run twice and must not
hand a name to a point that has changed since the changeset was written.
"""
import io
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from mountains.models import NamedPoint, PointName, Summit
from mountains.test_factories import make_col, make_country, make_summit
from mountains.test_point_names import make_language


def changeset(parent, col_point):
    return dict(
        sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
        summits=[dict(
            key='lidar:48.5574,21.3796', lat=48.5574, lon=21.3796, name='Szurok-hegy',
            names=[dict(lang='hu', name='Szurok-hegy', local=True)],
            altitude=643.67, datum='Bpv', countries=['hu'], prominence=223.5,
            parent=dict(pk=parent.pk, name=parent.point.name), notes=['Key col from Copernicus.'],
            col=dict(lat=48.5518, lon=21.3975, name=None, names=[], altitude=420.1, datum='Bpv',
                     countries=['sk'], notes=[]))],
        point_names=[dict(point=parent.point.pk, point_name=parent.point.name, lang='hu',
                          name='Nagy-Milics', what='summit')],
        col_names=[dict(point=col_point.pk, summit='Gánok', name='Rumanove sedlo', lang='sk',
                        names=[dict(lang='pl', name='Rumanowa Przełęcz')])])


class ApplyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        for code, priority in (('sk', 1), ('hu', 40), ('pl', 50)):
            make_language(code, priority)
        make_country('hu'), make_country('sk')
        cls.parent = make_summit('Veľký Milič', 894.7, 48.5843, 21.4581)
        cls.col = make_col(None, 2282.0, 49.1744, 20.1002)

    def run_it(self, data, *args):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump(data, f)
        out = io.StringIO()
        call_command('apply_lidar_changeset', f.name, *args, stdout=out)
        return out.getvalue()

    def test_the_summit_comes_with_its_key_col_parent_names_and_note(self):
        self.run_it(changeset(self.parent, self.col.point))
        summit = Summit.objects.get(point__name='Szurok-hegy')
        self.assertEqual(summit.prominence_parent, self.parent)
        self.assertAlmostEqual(summit.key_col.point.altitude, 420.1)
        self.assertEqual(summit.point.altitude_datum.code, 'Bpv')
        self.assertEqual([c.code for c in summit.point.countries.all()], ['hu'])
        self.assertEqual(summit.point.notes.count(), 1)
        self.assertTrue(PointName.objects.filter(point=summit.point, name='Szurok-hegy', local=True).exists())
        self.assertTrue(PointName.objects.filter(point=self.parent.point, name='Nagy-Milics').exists())
        self.col.point.refresh_from_db()
        self.assertEqual(self.col.point.name, 'Rumanove sedlo')

    def test_a_dry_run_leaves_nothing_behind(self):
        out = self.run_it(changeset(self.parent, self.col.point), '--dry-run')
        self.assertIn('rolled back', out)
        self.assertFalse(NamedPoint.objects.filter(name='Szurok-hegy').exists())

    def test_a_second_run_changes_nothing(self):
        data = changeset(self.parent, self.col.point)
        self.run_it(data)
        before = (Summit.objects.count(), PointName.objects.count())
        self.run_it(data)
        self.assertEqual((Summit.objects.count(), PointName.objects.count()), before)

    def test_a_point_renamed_since_is_left_alone(self):
        data = changeset(self.parent, self.col.point)
        data['point_names'][0]['point_name'] = 'Something else'
        out = self.run_it(data)
        self.assertIn('no longer', out)
        self.assertFalse(PointName.objects.filter(name='Nagy-Milics').exists())

    def test_a_taken_name_is_numbered(self):
        make_summit('Szurok-hegy', 500.0, 47.0, 21.0)
        make_summit('Szurok-hegy (2)', 500.0, 47.1, 21.0)
        self.run_it(changeset(self.parent, self.col.point))
        new = Summit.objects.get(point__name='Szurok-hegy (3)')
        self.assertTrue(PointName.objects.filter(point=new.point, name='Szurok-hegy').exists())

    def test_a_point_an_earlier_run_left_unnamed_gets_its_number(self):
        data = changeset(self.parent, self.col.point)
        self.run_it(data)
        new = Summit.objects.get(point__name='Szurok-hegy')
        NamedPoint.objects.filter(pk=new.point.pk).update(name=None)
        make_summit('Szurok-hegy', 500.0, 47.0, 21.0)
        self.run_it(data)
        new.point.refresh_from_db()
        self.assertEqual(new.point.name, 'Szurok-hegy (2)')


class SplitTests(TestCase):
    """A summit whose key col belongs to a higher top beside it: the top is split off."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        make_country('sk')
        cls.grand = make_summit('Veľká Vápenica', 1691.0, 48.914, 19.981)
        cls.old = make_summit('Panská hoľa', 1429.45, 48.9339, 20.0595,
                              key_col=make_col('Budnárka', 1295.2, 48.9109, 20.0255),
                              prominence_parent=cls.grand)
        cls.child = make_summit('Košariská', 1361.0, 48.95, 20.07,
                                key_col=make_col(None, 1300.0, 48.94, 20.07), prominence_parent=cls.old)

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), splits=[dict(
            summit=self.old.pk, summit_name='Panská hoľa',
            top=dict(lat=48.9205, lon=20.0458, altitude=1434.2, datum='Bpv', countries=['sk'], notes=['split']),
            col=dict(lat=48.9238, lon=20.0500, altitude=1384.64, datum='Bpv', countries=['sk']))])

    def test_the_new_top_takes_the_key_col_and_the_old_summit_hangs_off_it(self):
        ApplyTests.run_it(self, self.data())
        self.old.refresh_from_db()
        top = self.old.prominence_parent
        self.assertIsNone(top.point.name)
        self.assertEqual(top.key_col.point.name, 'Budnárka')
        self.assertEqual(top.prominence_parent, self.grand)
        self.assertAlmostEqual(self.old.compute_prominence(), 1429.45 - 1384.64)
        self.child.refresh_from_db()
        self.assertEqual(self.child.prominence_parent, top)      # P 61 > 44.8: no longer under Panská hoľa

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        before = Summit.objects.count()
        ApplyTests.run_it(self, self.data())
        self.assertEqual(Summit.objects.count(), before)


class FixTests(TestCase):
    """Hlinka moved to its lidar saddle; Veľká Rača and Minčol Oravský trading key cols."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.pilsko = make_summit('Pilsko', 1556.1, 49.528, 19.317, key_col=make_col('Hliny', 808.3, 49.6, 19.4))
        cls.hlinka = make_col('Hlinka', 847.0, 49.457375, 19.226643)
        cls.beskyd = make_col('Beskyd', 843.5, 49.3594, 19.1491)
        cls.mincol = make_summit('Minčol Oravský', 1395.3, 49.27, 19.25, key_col=cls.beskyd,
                                 prominence_parent=cls.pilsko)
        cls.raca = make_summit('Veľká Rača', 1236.6, 49.42, 19.10, key_col=cls.hlinka,
                               prominence_parent=cls.pilsko)

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
                    points=[dict(point=self.hlinka.point.pk, name='Hlinka', lat=49.457304, lon=19.226774,
                                 altitude=840.8, datum='Bpv')],
                    relinks=[dict(summit=self.mincol.pk, summit_name='Minčol Oravský',
                                  key_col=dict(point=self.hlinka.point.pk, name='Hlinka'),
                                  parent=dict(pk=self.pilsko.pk, name='Pilsko')),
                             dict(summit=self.raca.pk, summit_name='Veľká Rača',
                                  key_col=dict(point=self.beskyd.point.pk, name='Beskyd'),
                                  parent=dict(pk=self.mincol.pk, name='Minčol Oravský'))])

    def test_the_cols_trade_places_and_hlinka_moves(self):
        ApplyTests.run_it(self, self.data())
        self.raca.refresh_from_db(), self.mincol.refresh_from_db(), self.hlinka.point.refresh_from_db()
        self.assertEqual((self.raca.key_col, self.raca.prominence_parent), (self.beskyd, self.mincol))
        self.assertEqual(self.mincol.key_col, self.hlinka)
        self.assertEqual(self.hlinka.point.altitude, 840.8)
        self.assertAlmostEqual(self.hlinka.point.location.y, 49.457304)

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        out = ApplyTests.run_it(self, self.data())
        self.assertIn('0 moved, 0 relinked', out)


class TieAndMoveTests(TestCase):
    """A split where the other top is no higher, and a move that leaves the height alone."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        make_country('sk')
        cls.parent = make_summit('Pilsko', 1556.1, 49.528, 19.317, key_col=make_col('Hliny', 808.3, 49.6, 19.4))
        cls.kycera = make_summit('Kýčera', 806.0, 49.2500, 19.3640, key_col=make_col('Sedlo', 700.0, 49.24, 19.36),
                                 prominence_parent=cls.parent)

    def test_a_top_no_higher_hangs_off_the_summit(self):
        ApplyTests.run_it(self, dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), splits=[dict(
            summit=self.kycera.pk, summit_name='Kýčera',
            top=dict(lat=49.250385, lon=19.364540, altitude=805.97, datum='Bpv', countries=['sk'], notes=[]),
            col=dict(lat=49.250024, lon=19.363206, altitude=802.2, datum='Bpv', countries=['sk']))]))
        self.kycera.refresh_from_db()
        self.assertEqual(self.kycera.prominence_parent, self.parent)     # untouched
        other = Summit.objects.get(prominence_parent=self.kycera)
        self.assertAlmostEqual(other.compute_prominence(), 805.97 - 802.2)

    def test_a_move_without_an_altitude_keeps_the_height_and_its_source(self):
        point = self.kycera.point
        ApplyTests.run_it(self, dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), points=[
            dict(point=point.pk, name='Kýčera', lat=49.2502, lon=19.3641)]))
        point.refresh_from_db()
        self.assertEqual(point.altitude, 806.0)
        self.assertIsNone(point.source)
        self.assertAlmostEqual(point.location.y, 49.2502)


class KeyColTests(TestCase):
    """A summit given the key col the lidar shows; the col it had, given to the summit that owns it."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        make_country('sk')
        cls.top = make_summit('Kráľova hoľa', 1944.9, 48.88, 20.14, key_col=make_col('Low', 300.0, 48.0, 20.0))
        cls.javorinka = make_col('Javorinka', 942.0, 48.795843, 20.135847)
        cls.fabova = make_summit('Fabova hoľa', 1439.3, 48.80, 20.10, key_col=cls.javorinka, prominence_parent=cls.top)

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
                    relinks=[dict(summit=self.fabova.pk, summit_name='Fabova hoľa', key_col=dict(new=dict(
                        lat=48.794998, lon=20.125914, altitude=928.3, datum='Bpv', name=None, names=[], countries=['sk'])))],
                    summits=[dict(key='lidar:48.801438,20.138944', lat=48.801438, lon=20.138944, name=None, names=[],
                                  altitude=1049.4, datum='Bpv', countries=['sk'], prominence=105.2,
                                  parent=dict(pk=self.fabova.pk, name='Fabova hoľa'), notes=[],
                                  col=dict(existing=dict(point=self.javorinka.point.pk, name='Javorinka')))])

    def test_the_col_changes_hands(self):
        ApplyTests.run_it(self, self.data())
        self.fabova.refresh_from_db()
        self.assertAlmostEqual(self.fabova.key_col.point.altitude, 928.3)
        self.assertEqual(self.fabova.prominence_parent, self.top)            # kept
        self.assertEqual(Summit.objects.get(key_col=self.javorinka).prominence_parent, self.fabova)

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        before = (Summit.objects.count(), NamedPoint.objects.count())
        ApplyTests.run_it(self, self.data())
        self.assertEqual((Summit.objects.count(), NamedPoint.objects.count()), before)


class TwinSwapTests(TestCase):
    """Two tops a centimetre apart: the higher one, found by position, takes over the key col."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.grand = make_summit('Turíkov Žiar', 851.4, 49.3, 19.4, key_col=make_col('Low', 300.0, 49.0, 19.0))
        cls.col = make_col(None, 697.2, 49.2467, 19.3681)
        cls.named = make_summit('Kýčera', 806.0, 49.249813, 19.362251, key_col=cls.col, prominence_parent=cls.grand)
        cls.twin = make_summit(None, 805.97, 49.250385, 19.364540, key_col=make_col(None, 802.16, 49.250024, 19.363206),
                               prominence_parent=cls.named)

    def test_the_higher_twin_becomes_the_summit(self):
        ApplyTests.run_it(self, dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
            points=[dict(point=self.named.point.pk, name='Kýčera', lat=49.249813, lon=19.362251,
                         altitude=805.96, datum='Bpv')],
            relinks=[dict(summit_at=dict(lat=49.250385, lon=19.364540), summit_name='twin',
                          key_col=dict(point=self.col.point.pk, name=None), parent=dict(pk=self.grand.pk, name='Turíkov Žiar')),
                     dict(summit=self.named.pk, summit_name='Kýčera', key_col=dict(at=dict(lat=49.250024, lon=19.363206)),
                          parent_at=dict(lat=49.250385, lon=19.364540))]))
        self.twin.refresh_from_db(), self.named.refresh_from_db()
        self.assertEqual((self.twin.key_col, self.twin.prominence_parent), (self.col, self.grand))
        self.assertEqual(self.named.prominence_parent, self.twin)
        self.assertAlmostEqual(self.named.compute_prominence(), 805.96 - 802.16)


class MergeTests(TestCase):
    """Two copies of Malý Minčol: the one with the links stays and takes the other's lidar values."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        sk = make_language('sk', 1)
        cls.parent = make_summit('Minčol', 1152.6, 49.2, 21.0, key_col=make_col('Low', 300.0, 49.0, 21.0))
        cls.keep = make_summit('Malý Minčol', 1054.4, 49.255005, 21.004153,
                               key_col=make_col('Uhlisko', 955.0, 49.240438, 21.002705), prominence_parent=cls.parent)
        cls.drop = make_summit('Malý Minčol Čergovský', 1054.37, 49.254981, 21.004143,
                               key_col=make_col('Uhlisko (Malý Minčol)', 953.68, 49.240571, 21.002796),
                               prominence_parent=cls.parent)
        cls.child = make_summit('Kid', 1000.0, 49.26, 21.01, key_col=make_col(None, 990.0, 49.26, 21.0),
                                prominence_parent=cls.drop)
        PointName.objects.create(point=cls.keep.point, language=sk, name='Malý Minčol')
        PointName.objects.create(point=cls.drop.point, language=sk, name='Malý Minčol')
        PointName.objects.create(point=cls.drop.point, language=sk, name='Malý Minčol Čergovský')

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), merges=[
            dict(keep=dict(point=self.keep.point.pk, name='Malý Minčol'),
                 drop=dict(point=self.drop.point.pk, name='Malý Minčol Čergovský'), take_position=True),
            dict(keep=dict(point=self.keep.key_col.point.pk, name='Uhlisko'),
                 drop=dict(point=self.drop.key_col.point.pk, name='Uhlisko (Malý Minčol)'), take_position=True)])

    def test_one_copy_is_left_with_everything(self):
        ApplyTests.run_it(self, self.data())
        self.keep.refresh_from_db(), self.child.refresh_from_db()
        self.assertFalse(NamedPoint.objects.filter(name__in=['Malý Minčol Čergovský', 'Uhlisko (Malý Minčol)']).exists())
        self.assertEqual(self.child.prominence_parent, self.keep)
        self.assertEqual(self.keep.point.altitude, 1054.37)
        self.assertEqual(self.keep.key_col.point.altitude, 953.68)
        self.assertEqual(sorted(self.keep.point.names.values_list('name', flat=True)),
                         ['Malý Minčol', 'Malý Minčol Čergovský'])

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 merged', ApplyTests.run_it(self, self.data()))
