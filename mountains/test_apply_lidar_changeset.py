"""
`apply_lidar_changeset`: the P100s and names the lidar comparison found, applied to a
database. It is run here and then on the server, so it must be safe to run twice and must not
hand a name to a point that has changed since the changeset was written.
"""
import io
import json
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.management import call_command
from django.test import TestCase

from mountains.map_zooms import refresh_map_zooms
from mountains.models import Col, NamedPoint, PointName, Source, Summit
from mountains.test_factories import make_col, make_country, make_point, make_summit
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
        col_names=[dict(point=col_point.pk, was=[49.1744, 20.1002], summit='Gánok', name='Rumanove sedlo', lang='sk',
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

    def test_a_height_from_a_list_keeps_the_position_and_names_its_source(self):
        point = self.kycera.point
        data = dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), points=[
            dict(point=point.pk, name='Kýčera', altitude=807.0, datum=None, source='andes.org.uk')])
        ApplyTests.run_it(self, data)
        point.refresh_from_db()
        self.assertEqual(point.altitude, 807.0)
        self.assertIsNone(point.altitude_datum)
        self.assertEqual(point.source.name, 'andes.org.uk')
        self.assertAlmostEqual(point.location.y, 49.25)
        self.assertIn('1 skipped', ApplyTests.run_it(self, data))


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


class NumbersAreNotSharedTests(TestCase):
    """
    An unnamed point is taken by its number only where it stands where the changeset saw it:
    another database may have a different unnamed point under the same number.
    """

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        make_country('sk')
        cls.col = make_col(None, 697.2, 49.2467, 19.3681)

    def move(self, **extra):
        return ApplyTests.run_it(self, dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), points=[dict(
            point=self.col.point.pk, name=None, lat=49.24672, lon=19.36812, altitude=695.0, datum='Bpv', **extra)]))

    def test_by_number_alone_it_is_left_alone(self):
        out = self.move()
        self.assertIn('no position to tell it by', out)
        self.col.point.refresh_from_db()
        self.assertEqual(self.col.point.altitude, 697.2)

    def test_somewhere_else_it_is_left_alone(self):
        out = self.move(was=[48.0, 21.0])            # the same number, another point
        self.assertIn('not where the changeset saw it', out)
        self.col.point.refresh_from_db()
        self.assertEqual(self.col.point.altitude, 697.2)

    def test_where_it_was_seen_it_moves(self):
        self.move(was=[49.2467, 19.3681])
        self.col.point.refresh_from_db()
        self.assertEqual(self.col.point.altitude, 695.0)


class RefusedRelinkTests(TestCase):
    """A relink the invariants refuse leaves the summit exactly as it was: its own key col too."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        make_country('sk')
        cls.top = make_summit('Kráľova hoľa', 1944.9, 48.88, 20.14, key_col=make_col('Low', 300.0, 48.0, 20.0))
        cls.javorinka = make_col('Javorinka', 942.0, 48.795843, 20.135847)
        cls.fabova = make_summit('Fabova hoľa', 1439.3, 48.80, 20.10, key_col=cls.javorinka, prominence_parent=cls.top)
        cls.small = make_summit('Kopa', 1100.0, 48.81, 20.11, key_col=make_col(None, 1090.0, 48.809, 20.109),
                                prominence_parent=cls.fabova)

    def test_the_summit_keeps_its_key_col(self):
        # Kopa (P10) under Fabova hoľa is fine; Fabova hoľa (P497) under Kopa is not.
        out = ApplyTests.run_it(self, dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), relinks=[dict(
            summit=self.fabova.pk, summit_name='Fabova hoľa', parent=dict(pk=self.small.pk, name='Kopa'),
            key_col=dict(new=dict(lat=48.794998, lon=20.125914, altitude=928.3, datum='Bpv', name=None,
                                  names=[], countries=['sk'])))]))
        self.assertIn('Not relinked', out)
        self.fabova.refresh_from_db()
        self.assertEqual(self.fabova.key_col, self.javorinka)
        self.assertEqual(self.fabova.prominence_parent, self.top)
        self.assertFalse(Col.objects.filter(point__altitude=928.3).exists(), 'the col made for it is gone again')


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
                          key_col=dict(point=self.col.point.pk, name=None, was=[49.2467, 19.3681]), parent=dict(pk=self.grand.pk, name='Turíkov Žiar')),
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


class IsolationTests(TestCase):
    """The nearest higher point and isolation parent, the parent found by position."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.twin = make_summit(None, 2422.1, 49.163667, 20.052188, key_col=make_col(None, 2235.0, 49.17, 20.03))
        cls.satan = make_summit('Satan', 2421.5, 49.163132, 20.052843, key_col=make_col(None, 2411.1, 49.1634, 20.0527),
                                prominence_parent=cls.twin, isolation_name='western ridge of Hrubý vrch')

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), isolations=[dict(
            summit=dict(pk=self.satan.pk, name='Satan'), parent=dict(at=[49.163667, 20.052188]),
            nhp=[49.163622, 20.05223], metres=70.4)])

    def test_the_twin_becomes_the_isolation_parent(self):
        ApplyTests.run_it(self, self.data())
        self.satan.refresh_from_db()
        self.assertEqual(self.satan.isolation_parent, self.twin)
        self.assertAlmostEqual(self.satan.nearest_higher_point.y, 49.163622)
        self.assertIsNone(self.satan.isolation_name)          # it described another parent's ground
        self.assertEqual(self.satan.isolation_source.name, 'DMR 5.0')

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 isolations', ApplyTests.run_it(self, self.data()))


class PromoteTests(TestCase):
    """Devínska Kobyla's name to the higher of its two tops, with what referred to it."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        sk = make_language('sk', 1)
        cls.higher = make_summit(None, 515.28, 48.189662, 16.995148, key_col=make_col('Lamač', 204.0, 48.19, 17.05))
        cls.lower = make_summit('Devínska Kobyla', 514.1, 48.189389, 16.995842,
                                key_col=make_col(None, 512.8, 48.1895, 16.9955), prominence_parent=cls.higher)
        PointName.objects.create(point=cls.lower.point, language=sk, name='Devínska Kobyla', local=True)
        cls.neighbour = make_summit('Sandberg', 300.0, 48.20, 16.98, isolation_parent=cls.lower)

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), promotions=[dict(
            lower=dict(pk=self.lower.pk, name='Devínska Kobyla'), higher=dict(at=[48.189662, 16.995148]))])

    def test_the_name_and_its_links_go_to_the_higher_top(self):
        ApplyTests.run_it(self, self.data())
        self.higher.point.refresh_from_db(), self.lower.point.refresh_from_db(), self.neighbour.refresh_from_db()
        self.lower.refresh_from_db()
        self.assertEqual(self.higher.point.name, 'Devínska Kobyla')
        self.assertIsNone(self.lower.point.name)
        self.assertEqual(self.lower.prominence_parent, self.higher)          # still hangs off it
        self.assertEqual(self.neighbour.isolation_parent, self.higher)
        self.assertTrue(PointName.objects.filter(point=self.higher.point, name='Devínska Kobyla').exists())

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 promoted', ApplyTests.run_it(self, self.data()))


class MergeTakeNameTests(TestCase):
    """A plain point and a new summit on it: the summit stays, under the plain name."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.plain = make_point('Králikov vrch', 918.48, 48.467017, 19.253111)
        cls.new = make_summit('Králikov vrch (2)', 918.5, 48.467011, 19.253107,
                              key_col=make_col(None, 877.0, 48.46, 19.25))

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), merges=[dict(
            keep=dict(point=None, name='Králikov vrch (2)'), drop=dict(point=self.plain.pk, name='Králikov vrch'),
            take_name=True)])

    def test_the_summit_takes_the_plain_name(self):
        ApplyTests.run_it(self, self.data())
        self.new.point.refresh_from_db()
        self.assertEqual(self.new.point.name, 'Králikov vrch')
        self.assertFalse(NamedPoint.objects.filter(pk=self.plain.pk).exists())

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 merged', ApplyTests.run_it(self, self.data()))


class MergeTwinTests(TestCase):
    """The name on the lower of two tops of one hill: the higher, unnamed, takes it; the dip goes."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.higher = make_summit(None, 950.98, 48.978764, 21.376892, key_col=make_col(None, 832.86, 48.97, 21.37))
        cls.dip = make_col(None, 947.92, 48.978783, 21.376578)
        cls.dip.point.source = Source.objects.create(name='DMR 5.0')
        cls.dip.point.save()
        cls.lower = make_summit('Nad Remetovou', 950.97, 48.978558, 21.376535, key_col=cls.dip)

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), merges=[dict(
            keep=dict(at=[48.978764, 21.376892], name=None), drop=dict(at=[48.978558, 21.376535], name='Nad Remetovou'),
            take_name=True, drop_key_col=True)])

    def test_the_higher_top_takes_the_name_and_the_dip_goes(self):
        ApplyTests.run_it(self, self.data())
        self.higher.point.refresh_from_db()
        self.assertEqual(self.higher.point.name, 'Nad Remetovou')
        self.assertAlmostEqual(self.higher.point.altitude, 950.98)
        self.assertFalse(Summit.objects.filter(pk=self.lower.pk).exists())
        self.assertFalse(Col.objects.filter(pk=self.dip.pk).exists())

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 merged', ApplyTests.run_it(self, self.data()))


class RenameTests(TestCase):
    """A summit named after a sign beside it gets its own name; the sign's goes."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.sk = make_language('sk', 1)
        cls.s = make_summit('Geotop, pieskovce', 344.07, 48.244014, 19.106256)

    def data(self):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), renames=[dict(
            at=[48.244014, 19.106256], old='Geotop, pieskovce', name='Čabradský hradný vrch', lang='sk')])

    def test_renamed(self):
        PointName.objects.create(point=self.s.point, language=self.sk,
                                 name='Geotop, pieskovce', local=True)
        ApplyTests.run_it(self, self.data())
        self.s.point.refresh_from_db()
        self.assertEqual(self.s.point.name, 'Čabradský hradný vrch')
        self.assertEqual(list(PointName.objects.filter(point=self.s.point).values_list('name', flat=True)),
                         ['Čabradský hradný vrch'])

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 renamed', ApplyTests.run_it(self, self.data()))


class CopernicusTests(TestCase):
    """South America from Copernicus: countries the database lacks, islands, heights from a list."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        make_language('es', 20)
        make_country('cl')

    def data(self):
        return dict(
            sources=dict(lidar='Copernicus GLO-30', names='OpenStreetMap'),
            reference=dict(
                countries=[dict(code='fk', name='Falkland Islands', full_name='Falkland Islands',
                                english_name='Falkland Islands')],
                languages=[dict(iso639_1='pt', country_code='br', name='Português', english_name='Portuguese',
                                priority=25)]),
            summits=[
                dict(key='glo:-51.69,-58.83', lat=-51.691806, lon=-58.834514, name='Mount Usborne',
                     names=[dict(lang='en', name='Mount Usborne', local=True)], altitude=703.2, datum='EGM2008',
                     countries=['fk'], prominence=703.2, parent=None, island=True, notes=[]),
                dict(key='glo:-27.11,-68.54', lat=-27.109722, lon=-68.541389, name='Ojos del Salado',
                     names=[dict(lang='es', name='Ojos del Salado', local=True)], altitude=6893.0, datum=None,
                     source='andes.org.uk', countries=['cl'], prominence=3684.1, parent=None, notes=[],
                     col=dict(lat=-27.0, lon=-68.0, name=None, names=[], altitude=3208.9, datum='EGM2008',
                              countries=['cl'], notes=[]))])

    def test_islands_lists_and_new_countries(self):
        ApplyTests.run_it(self, self.data())
        usborne = Summit.objects.get(point__name='Mount Usborne')
        self.assertTrue(usborne.island_high_point)
        self.assertIsNone(usborne.key_col)
        self.assertEqual(list(usborne.point.countries.values_list('code', flat=True)), ['fk'])
        self.assertEqual(usborne.point.source.name, 'Copernicus GLO-30')
        ojos = Summit.objects.get(point__name='Ojos del Salado')
        self.assertEqual(ojos.point.source.name, 'andes.org.uk')
        self.assertIsNone(ojos.point.altitude_datum)
        self.assertEqual(ojos.key_col.point.altitude_datum.code, 'EGM2008')
        self.assertEqual(ojos.key_col.point.source.name, 'Copernicus GLO-30')

    def test_a_height_system_the_database_lacks_comes_with_the_changeset(self):
        data = self.data()
        data['reference']['datums'] = [dict(code='TWVD2001', name='Taiwan Vertical Datum 2001',
                                            crs='EPSG:3824+8904', kind='orthometric', notes='No grid to EGM2008.')]
        data['summits'][0]['datum'] = 'TWVD2001'
        ApplyTests.run_it(self, data)
        point = Summit.objects.get(point__name='Mount Usborne').point
        self.assertEqual(point.altitude_datum.code, 'TWVD2001')
        self.assertIsNone(point.altitude_egm2008)      # no conversion, never the raw number
        self.assertIn('0 summits', ApplyTests.run_it(self, data))

    def test_a_dry_run_leaves_no_countries_behind(self):
        ApplyTests.run_it(self, self.data(), '--dry-run')
        self.assertFalse(Summit.objects.filter(point__name='Mount Usborne').exists())
        from core.models import Country
        self.assertFalse(Country.objects.filter(code='fk').exists())

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 summits', ApplyTests.run_it(self, self.data()))


class RemoveSummitTests(TestCase):
    """A top only because the lidar stops at the border: removed, by position, and only that."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        lidar = Source.objects.create(name='DMR 5.0')
        cls.parent = make_summit('Lysá hora', 295.65, 48.505128, 21.577336)
        cls.col = make_col(None, 229.43, 48.503896, 21.584335)
        cls.col.point.source = lidar
        cls.col.point.save()
        cls.edge = make_summit(None, 243.66, 48.50607, 21.586702, key_col=cls.col, prominence_parent=cls.parent)
        cls.edge.point.source = lidar
        cls.edge.point.save()
        cls.lower = make_summit(None, 191.94, 48.508, 21.59, isolation_parent=cls.edge,
                                nearest_higher_point=Point(21.593085, 48.51121, srid=4326),
                                isolation_name='its southern slope')

    def data(self, at=(48.50607, 21.586702)):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'),
                    remove_summits=[dict(at=list(at), why='the ground rises on into Hungary')])

    def test_the_summit_and_its_key_col_go_and_the_isolation_is_kept_unattributed(self):
        refresh_map_zooms()             # its row in the map's zooms is no reason to keep it
        out = ApplyTests.run_it(self, self.data())
        self.assertIn('1 summits removed', out)
        self.assertFalse(Summit.objects.filter(pk=self.edge.pk).exists())
        self.assertFalse(Col.objects.filter(pk=self.col.pk).exists())
        self.lower.refresh_from_db()
        self.assertIsNone(self.lower.isolation_parent)
        self.assertIsNone(self.lower.isolation_name)
        self.assertAlmostEqual(self.lower.nearest_higher_point.y, 48.51121)
        self.assertIn('isolation parent cleared', self.lower.point.notes.get().text.lower())

    def test_a_second_run_changes_nothing(self):
        ApplyTests.run_it(self, self.data())
        self.assertIn('0 summits removed', ApplyTests.run_it(self, self.data()))

    def test_a_prominence_child_keeps_it(self):
        make_summit(None, 240.0, 48.507, 21.588, key_col=make_col(None, 235.0, 48.5065, 21.5875),
                    prominence_parent=self.edge)
        out = ApplyTests.run_it(self, self.data())
        self.assertIn('not removed', out)
        self.assertTrue(Summit.objects.filter(pk=self.edge.pk).exists())

    def test_a_named_summit_is_never_taken(self):
        self.edge.point.name = 'Somár'
        self.edge.point.save()
        self.assertIn('0 summits removed', ApplyTests.run_it(self, self.data()))
        self.assertTrue(Summit.objects.filter(pk=self.edge.pk).exists())


class LineParentTests(TestCase):
    """Line parents by position, with the cutoff and the data they were worked out from."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.top = make_summit('Top', 2000, 49.0, 20.0, island_high_point=True)
        cls.hump = make_summit('Hump', 1500, 49.05, 20.05, key_col=make_col(None, 1400, 49.03, 20.03),
                               prominence_parent=cls.top)
        cls.spur = make_summit('Spur', 1450, 49.06, 20.06, key_col=make_col(None, 1300, 49.055, 20.055),
                               prominence_parent=cls.top)

    def data(self, *links):
        return dict(sources=dict(lidar='DMR 5.0', names='OpenStreetMap'), line_parents=list(links))

    def test_the_first_higher_not_the_more_prominent(self):
        out = ApplyTests.run_it(self, self.data(
            dict(summit_at=[49.06, 20.06], parent_at=[49.05, 20.05], cutoff=30),
            dict(summit_at=[49.0, 20.0], parent_at=None, cutoff=30)))
        self.assertIn('2 line parents', out)
        self.spur.refresh_from_db()
        self.top.refresh_from_db()
        self.assertEqual(self.spur.line_parent, self.hump)
        self.assertEqual((self.spur.line_cutoff, self.spur.line_source.name), (30, 'DMR 5.0'))
        self.assertIsNone(self.top.line_parent)          # looked: an island has none
        self.assertEqual(self.top.line_cutoff, 30)

    def test_a_second_run_changes_nothing(self):
        data = self.data(dict(summit_at=[49.06, 20.06], parent_at=[49.05, 20.05], cutoff=30))
        ApplyTests.run_it(self, data)
        self.assertIn('0 line parents', ApplyTests.run_it(self, data))

    def test_a_lower_parent_is_refused(self):
        out = ApplyTests.run_it(self, self.data(dict(summit_at=[49.05, 20.05], parent_at=[49.06, 20.06], cutoff=30)))
        self.assertIn('Line parent not set', out)
        self.hump.refresh_from_db()
        self.assertIsNone(self.hump.line_parent)

    def test_nobody_where_the_changeset_says_is_skipped(self):
        out = ApplyTests.run_it(self, self.data(dict(summit_at=[49.06, 20.06], parent_at=[49.5, 20.5], cutoff=30)))
        self.assertIn('no summit at', out)


class MoveBySummitAtTests(TestCase):
    """A height fix for a summit found where it stands: numbers are not shared with amos."""

    @classmethod
    def setUpTestData(cls):
        get_user_model().objects.create_superuser('admin', 'admin@example.invalid', 'x')
        cls.laguna = make_summit('Laguna', 5034.0, -34.110694, -69.629028)

    def data(self, at):
        return dict(sources=dict(lidar='Copernicus GLO-30', names='OpenStreetMap'),
                    points=[dict(summit_at=at, altitude=5071.9, source='Copernicus GLO-30')])

    def test_the_summit_there_takes_the_height(self):
        self.assertIn('1 moved', ApplyTests.run_it(self, self.data([-34.110694, -69.629028])))
        self.laguna.point.refresh_from_db()
        self.assertEqual(self.laguna.point.altitude, 5071.9)
        self.assertEqual(self.laguna.point.source.name, 'Copernicus GLO-30')
        self.assertIn('0 moved', ApplyTests.run_it(self, self.data([-34.110694, -69.629028])))

    def test_nobody_there_is_skipped(self):
        out = ApplyTests.run_it(self, self.data([-34.2, -69.7]))
        self.assertIn('not moved', out)
        self.laguna.point.refresh_from_db()
        self.assertEqual(self.laguna.point.altitude, 5034.0)
