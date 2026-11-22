"""`mountains.slope_horizon`: the stored slope and horizon parents, as the admin actions and the command write them."""
from django.test import TestCase

from mountains.models import Summit
from mountains.slope_horizon import compute
from mountains.test_factories import make_summit
from mountains.views.cache import data_version


class ComputeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.top = make_summit('Top', 2000, 49.0, 20.0)
        cls.near = make_summit('Near', 1900, 49.02, 20.0)        # steep and close above Low
        cls.low = make_summit('Low', 1000, 49.03, 20.0)

    def test_parents_and_stamps(self):
        with self.captureOnCommitCallbacks(execute=True):
            before = data_version()
            changed = compute('slope')
        self.assertNotEqual(data_version(), before)          # the map and pages are told
        self.assertCountEqual(changed, [self.near.pk, self.low.pk])
        for s in Summit.objects.all():
            self.assertIsNotNone(s.slope_computed)            # looked at, parent or not
        self.assertEqual(Summit.objects.get(pk=self.low.pk).slope_parent_id, self.near.pk)
        self.assertIsNone(Summit.objects.get(pk=self.top.pk).slope_parent_id)   # nothing higher

    def test_only_those_asked_for(self):
        compute('horizon', Summit.objects.filter(pk=self.low.pk))
        self.assertIsNotNone(Summit.objects.get(pk=self.low.pk).horizon_parent_id)
        self.assertIsNone(Summit.objects.get(pk=self.near.pk).horizon_computed)

    def test_a_second_run_changes_nothing(self):
        compute('horizon_std')
        self.assertEqual(compute('horizon_std'), [])


class CommandProgressTests(TestCase):
    """`manage.py compute_slope_horizon` says where it is: the phases, then the search to 100 %."""

    @classmethod
    def setUpTestData(cls):
        for i in range(5):
            make_summit(f'S{i}', 1000.0 + 100 * i, 49.0 + 0.01 * i, 20.0)

    def test_it_reports_the_search_and_the_storing(self):
        import io
        from unittest import mock
        from django.core.management import call_command
        out, err = io.StringIO(), io.StringIO()
        with mock.patch('sys.stderr', err):
            call_command('compute_slope_horizon', '--kind', 'slope', stdout=out)
        bars = err.getvalue()
        self.assertIn('slope searching', bars)
        self.assertIn('slope storing', bars)
        self.assertIn('100.0 %', bars)
        self.assertIn('5/5', bars)
        self.assertIn('slope: 4 changed in', out.getvalue())   # the highest has no parent before or after

    def test_progress_comes_every_thousand_summits_and_at_the_end(self):
        from core.functions.parents import slope_parents
        rows = [(i, 49.0 + i * 1e-4, 20.0, 1000.0 + i) for i in range(2500)]
        seen = []
        slope_parents(rows, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1000, 2500), (2000, 2500), (2500, 2500)])
