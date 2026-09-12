"""
Tests for the range hierarchy.

What is pinned here is `path` — the one piece of denormalised state in the project, the
thing every containment query reads, and the thing that goes quietly wrong when a range is
re-parented. A stale path does not raise: it returns a subtly wrong set of summits, which is
the kind of error this database exists to avoid.

The second theme is the split between `save()` and `clean()`. `save()` never validates in
this project, so anything whose absence would leave the tree *unrepresentable* rather than
merely wrong has to be guarded in `save()` as well.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from mountains.models import Range, SummitRange
from mountains.test_factories import (assign_range, make_range, make_range_system,
                                      make_summit)


class RangeTestCase(TestCase):
    """
    One system with a four-deep chain and a sibling branch:

        Karpaty
          Západné Karpaty
            Tatry
              Vysoké Tatry
          Východné Karpaty
    """

    @classmethod
    def setUpTestData(cls):
        cls.system = make_range_system('sk-geomorf', 'Geomorfologické členenie Slovenska')
        cls.karpaty = make_range(cls.system, 'Karpaty', level_name='sústava')
        cls.zapadne = make_range(cls.system, 'Západné Karpaty', cls.karpaty,
                                 level_name='subsústava')
        cls.tatry = make_range(cls.system, 'Tatry', cls.zapadne, level_name='celok')
        cls.vysoke = make_range(cls.system, 'Vysoké Tatry', cls.tatry, level_name='podcelok')
        cls.vychodne = make_range(cls.system, 'Východné Karpaty', cls.karpaty,
                                  level_name='subsústava')

    @staticmethod
    def updates_while(action):
        """
        How many UPDATEs `action` issues. Counting statements rather than using
        `assertNumQueries` because `save()` opens a transaction, and inside a `TestCase`
        that is a SAVEPOINT/RELEASE pair which would otherwise be counted as work.
        """
        with CaptureQueriesContext(connection) as captured:
            action()
        return len([q for q in captured.captured_queries if q['sql'].startswith('UPDATE')])


class PathTests(RangeTestCase):
    def test_a_root_carries_only_its_own_id(self):
        self.assertEqual(self.karpaty.path, f'{self.karpaty.pk}.')

    def test_a_child_extends_its_parents_path(self):
        self.assertEqual(self.zapadne.path, f'{self.karpaty.path}{self.zapadne.pk}.')

    def test_a_deep_node_carries_the_whole_chain(self):
        self.assertEqual(
            self.vysoke.path,
            f'{self.karpaty.pk}.{self.zapadne.pk}.{self.tatry.pk}.{self.vysoke.pk}.')

    def test_the_path_reaches_the_database_not_just_the_instance(self):
        """Creation is two writes; this is the one that proves the second happened."""
        self.assertEqual(Range.objects.get(pk=self.vysoke.pk).path, self.vysoke.path)

    def test_the_trailing_dot_keeps_a_longer_id_out_of_the_subtree(self):
        """
        The crux of the separator: without it, '4.' prefixes '41.' and one root swallows
        another's whole tree.

        The paths are written directly rather than by forcing primary keys — a sequence
        advances across test classes and is not rolled back with them, so `pk=4` collides
        with whatever the fixture already took.
        """
        system = make_range_system('ids', 'forced ids')
        four = make_range(system, 'four')
        forty_one = make_range(system, 'forty-one')
        child = make_range(system, 'child of four', four)

        Range.objects.filter(pk=four.pk).update(path='4.')
        Range.objects.filter(pk=forty_one.pk).update(path='41.')
        Range.objects.filter(pk=child.pk).update(path='4.7.')
        four.refresh_from_db()

        self.assertIn(child, four.descendants())
        self.assertNotIn(forty_one, four.self_and_descendants())

    def test_ordering_keeps_every_subtree_contiguous(self):
        """
        `Meta.ordering` walks each tree depth-first, which only holds because `path` carries
        the C collation — under this database's en_GB.UTF-8, '30.' sorts between '3.' and
        '3.41.' and the subtrees interleave.
        """
        names = [r.name for r in Range.objects.filter(system=self.system)]
        self.assertEqual(names, ['Karpaty', 'Západné Karpaty', 'Tatry', 'Vysoké Tatry',
                                 'Východné Karpaty'])

    def test_saving_an_unrelated_field_leaves_the_path_alone(self):
        """One write, not two: nothing moved, so there is no path to rewrite."""
        self.tatry.name = 'Tatry (renamed)'
        self.assertEqual(self.updates_while(self.tatry.save), 1)
        self.assertEqual(Range.objects.get(pk=self.tatry.pk).path, self.tatry.path)

    def test_a_path_without_its_terminator_is_refused(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Range.objects.filter(pk=self.tatry.pk).update(path='1')


class ReparentingTests(RangeTestCase):
    def test_moving_a_node_rewrites_its_own_path(self):
        self.tatry.parent = self.vychodne
        self.tatry.save()
        self.assertEqual(Range.objects.get(pk=self.tatry.pk).path,
                         f'{self.vychodne.path}{self.tatry.pk}.')

    def test_moving_a_node_rewrites_every_descendant(self):
        self.tatry.parent = self.vychodne
        self.tatry.save()
        self.vysoke.refresh_from_db()
        self.assertEqual(self.vysoke.path,
                         f'{self.vychodne.path}{self.tatry.pk}.{self.vysoke.pk}.')

    def test_moving_a_node_to_the_top_shortens_its_subtree(self):
        self.tatry.parent = None
        self.tatry.save()
        self.vysoke.refresh_from_db()
        self.assertEqual(self.vysoke.path, f'{self.tatry.pk}.{self.vysoke.pk}.')

    def test_a_sibling_subtree_is_untouched(self):
        before = Range.objects.get(pk=self.vychodne.pk).path
        self.tatry.parent = self.vychodne
        self.tatry.save()
        self.assertEqual(Range.objects.get(pk=self.vychodne.pk).path, before)

    def test_the_subtree_is_rewritten_in_one_statement(self):
        """The write count must not grow with the size of the subtree that moves."""
        for index in range(10):
            make_range(self.system, f'extra {index}', self.vysoke)

        self.tatry.parent = self.vychodne
        # Its own row, then its path, then one prefix swap across the whole subtree.
        self.assertEqual(self.updates_while(self.tatry.save), 3)

    def test_moving_a_range_does_not_touch_a_single_summit_row(self):
        """The headline invariant: containment is a property of ranges, not of summits."""
        peak = make_summit('Gerlach', 2655.0, 49.16, 20.13)
        assign_range(peak, self.vysoke)
        before = list(SummitRange.objects.values_list('pk', 'summit_id', 'range_id',
                                                      'system_id'))

        self.tatry.parent = self.vychodne
        self.tatry.save()

        self.assertEqual(list(SummitRange.objects.values_list('pk', 'summit_id', 'range_id',
                                                              'system_id')), before)
        self.assertIn(peak, Range.objects.get(pk=self.vychodne.pk).all_summits())

    def test_a_hand_built_instance_cannot_blank_out_the_tree(self):
        """
        `Range(pk=..., ...)` carries no path, and rewriting descendants from an empty prefix
        would match the entire table. `save()` reads the stored path instead.
        """
        rebuilt = Range(pk=self.tatry.pk, system=self.system, name='Tatry',
                        parent=self.vychodne)
        rebuilt.save()
        self.vysoke.refresh_from_db()
        self.assertEqual(self.vysoke.path,
                         f'{self.vychodne.path}{self.tatry.pk}.{self.vysoke.pk}.')

    def test_rebuild_paths_repairs_a_corrupted_tree(self):
        expected = {r.pk: r.path for r in Range.objects.all()}
        Range.objects.update(path='')
        Range.rebuild_paths()
        self.assertEqual({r.pk: r.path for r in Range.objects.all()}, expected)


class CycleTests(RangeTestCase):
    def test_a_range_cannot_be_its_own_parent(self):
        self.tatry.parent = self.tatry
        with self.assertRaises(ValidationError) as caught:
            self.tatry.full_clean()
        self.assertIn('parent', caught.exception.error_dict)

    def test_a_range_cannot_be_parented_to_its_own_descendant(self):
        self.tatry.parent = self.vysoke
        with self.assertRaises(ValidationError) as caught:
            self.tatry.full_clean()
        self.assertIn('parent', caught.exception.error_dict)

    def test_a_legal_move_validates(self):
        self.tatry.parent = self.vychodne
        self.tatry.full_clean()

    def test_save_refuses_a_cycle_even_though_it_does_not_validate(self):
        """
        `save()` never calls `clean()` here, and a cycle is not a merely-wrong record — it
        is a subtree whose paths cannot be expressed. So `save()` stops too, before writing.
        """
        before = Range.objects.get(pk=self.vysoke.pk).path
        self.tatry.parent = self.vysoke
        with self.assertRaises(ValueError):
            self.tatry.save()
        self.assertEqual(Range.objects.get(pk=self.vysoke.pk).path, before)


class SystemTests(RangeTestCase):
    def test_a_parent_from_another_system_is_refused(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        alps = make_range(soiusa, 'Alpi')
        self.tatry.parent = alps
        with self.assertRaises(ValidationError) as caught:
            self.tatry.full_clean()
        self.assertIn('parent', caught.exception.error_dict)

    def test_a_saved_range_cannot_change_system(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        self.karpaty.system = soiusa
        with self.assertRaises(ValidationError) as caught:
            self.karpaty.full_clean()
        self.assertIn('system', caught.exception.error_dict)

    def test_two_systems_can_name_the_same_mountains(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        twin = make_range(soiusa, 'Tatry')
        self.assertNotEqual(twin.pk, self.tatry.pk)

    def test_two_children_of_one_parent_cannot_share_a_name(self):
        with transaction.atomic(), self.assertRaises(IntegrityError):
            make_range(self.system, 'Tatry', self.zapadne)

    def test_two_roots_cannot_share_a_name_either(self):
        """`parent` is NULL on roots, so this only bites because of nulls_distinct=False."""
        with transaction.atomic(), self.assertRaises(IntegrityError):
            make_range(self.system, 'Karpaty')


class MembershipTests(RangeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.peak = make_summit('Gerlach', 2655.0, 49.16, 20.13)

    def test_the_system_is_copied_from_the_range(self):
        membership = assign_range(self.peak, self.vysoke)
        self.assertEqual(membership.system_id, self.system.pk)

    def test_a_summit_gets_one_range_per_system(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        assign_range(self.peak, self.vysoke)
        assign_range(self.peak, make_range(soiusa, 'Tatry'))
        self.assertEqual(self.peak.range_memberships.count(), 2)

    def test_a_second_range_in_the_same_system_is_refused(self):
        assign_range(self.peak, self.vysoke)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            assign_range(self.peak, self.tatry)

    def test_a_membership_whose_system_disagrees_is_refused(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        membership = SummitRange(summit=self.peak, range=self.vysoke, system=soiusa)
        with self.assertRaises(ValidationError) as caught:
            membership.full_clean()
        self.assertIn('range', caught.exception.error_dict)

    def test_a_membership_dies_with_its_summit(self):
        assign_range(self.peak, self.vysoke)
        self.peak.delete()
        self.assertEqual(SummitRange.objects.count(), 0)

    def test_a_range_holding_summits_cannot_be_deleted(self):
        assign_range(self.peak, self.vysoke)
        with transaction.atomic(), self.assertRaises(Exception):
            self.vysoke.delete()

    def test_a_range_with_children_cannot_be_deleted(self):
        with transaction.atomic(), self.assertRaises(Exception):
            self.tatry.delete()


class NavigationTests(RangeTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gerlach = make_summit('Gerlach', 2655.0, 49.16, 20.13)
        cls.krivan = make_summit('Kriváň', 2494.0, 49.16, 19.99)
        assign_range(cls.gerlach, cls.vysoke)          # on the deepest range
        assign_range(cls.krivan, cls.tatry)            # one level up

    def test_ancestors_run_root_first(self):
        self.assertEqual([r.name for r in self.vysoke.ancestors()],
                         ['Karpaty', 'Západné Karpaty', 'Tatry'])

    def test_ancestors_cost_one_query_however_deep(self):
        with self.assertNumQueries(1):
            list(self.vysoke.ancestors())

    def test_a_root_has_no_ancestors(self):
        self.assertEqual(list(self.karpaty.ancestors()), [])

    def test_depth_counts_from_zero_and_costs_nothing(self):
        with self.assertNumQueries(0):
            self.assertEqual(self.karpaty.depth(), 0)
            self.assertEqual(self.vysoke.depth(), 3)

    def test_all_summits_reaches_through_the_subtree(self):
        """A summit recorded on a sub-range still belongs to the ranges above it."""
        self.assertCountEqual(self.tatry.all_summits(), [self.gerlach, self.krivan])
        self.assertCountEqual(self.karpaty.all_summits(), [self.gerlach, self.krivan])
        self.assertCountEqual(self.vysoke.all_summits(), [self.gerlach])

    def test_direct_memberships_are_not_the_same_as_all_summits(self):
        self.assertEqual([m.summit for m in self.tatry.memberships.all()], [self.krivan])

    def test_high_point_is_the_highest_in_the_whole_subtree(self):
        self.assertEqual(self.karpaty.high_point(), self.gerlach)
        self.assertEqual(self.vysoke.high_point(), self.gerlach)

    def test_high_point_of_an_empty_range_is_none(self):
        self.assertIsNone(self.vychodne.high_point())

    def test_an_unsaved_range_has_an_empty_subtree(self):
        """An empty path would otherwise prefix-match the entire table."""
        self.assertEqual(list(Range(name='nowhere').self_and_descendants()), [])
        self.assertEqual(list(Range(name='nowhere').all_summits()), [])
