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
