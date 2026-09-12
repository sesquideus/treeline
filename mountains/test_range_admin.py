"""
Tests for entering ranges through the admin, which is how this data gets in.

`manage.py check` — run as a test in `test_system_checks.py` — already validates every
cairn `list_display` directive, every `fieldsets` reference and every `autocomplete_fields`
that lacks `search_fields` on the far side. So nothing here re-tests the declarative surface.
What it does test is the three things a check cannot see: that the bulk action updates rather
than duplicates, that it does not quietly drop most of a "select all" selection, and that the
pages actually render — an MRO or widget mistake passes `check` without a murmur.
"""

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.test import TestCase
from django.urls import reverse

from mountains.admin.range import RangeAdmin
from mountains.models import Range, SummitRange
from mountains.test_factories import (assign_range, make_range, make_range_system,
                                      make_summit)
from users.models import User


class RangeAdminTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('curator', 'curator@example.com', 'secret')
        cls.system = make_range_system('sk-geomorf', 'Geomorfologické členenie Slovenska')
        cls.tatry = make_range(cls.system, 'Tatry', level_name='celok')
        cls.vysoke = make_range(cls.system, 'Vysoké Tatry', cls.tatry, level_name='podcelok')
        cls.zapadne = make_range(cls.system, 'Západné Tatry', cls.tatry, level_name='podcelok')
        cls.gerlach = make_summit('Gerlach', 2655.0, 49.16, 20.13)
        cls.krivan = make_summit('Kriváň', 2494.0, 49.16, 19.99)

    def setUp(self):
        self.client.force_login(self.user)

    def assign(self, summits, target, **extra):
        return self.client.post(reverse('admin:mountains_summit_changelist'), {
            'action': 'assign_to_range',
            ACTION_CHECKBOX_NAME: [str(s.pk) for s in summits],
            'apply': '1',
            'range': str(target.pk),
            **extra,
        }, follow=True)


class AssignActionTests(RangeAdminTestCase):
    def test_assigning_an_unplaced_summit_creates_one_membership(self):
        self.assign([self.gerlach], self.vysoke)
        membership = SummitRange.objects.get(summit=self.gerlach)
        self.assertEqual(membership.range, self.vysoke)
        self.assertEqual(membership.system, self.system, 'the system must be derived')

    def test_reassigning_moves_the_membership_instead_of_adding_one(self):
        """
        The load-bearing one. `unique(summit, system)` means a naive create raises the first
        time anyone corrects a mistake, which is the first thing a curator does.
        """
        assign_range(self.gerlach, self.zapadne)
        self.assign([self.gerlach], self.vysoke)

        self.assertEqual(SummitRange.objects.filter(summit=self.gerlach).count(), 1)
        self.assertEqual(SummitRange.objects.get(summit=self.gerlach).range, self.vysoke)

    def test_a_second_classification_gets_its_own_membership(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        assign_range(self.gerlach, self.vysoke)
        self.assign([self.gerlach], make_range(soiusa, 'Tatra'))

        self.assertEqual(SummitRange.objects.filter(summit=self.gerlach).count(), 2)

    def test_a_mixed_selection_assigns_and_moves_in_one_pass(self):
        assign_range(self.krivan, self.zapadne)
        self.assign([self.gerlach, self.krivan], self.vysoke)

        self.assertEqual(
            set(SummitRange.objects.values_list('summit_id', 'range_id')),
            {(self.gerlach.pk, self.vysoke.pk), (self.krivan.pk, self.vysoke.pk)})

    def test_the_first_post_only_confirms_and_writes_nothing(self):
        """An action that fires on the first POST would skip the range chooser entirely."""
        response = self.client.post(reverse('admin:mountains_summit_changelist'), {
            'action': 'assign_to_range',
            ACTION_CHECKBOX_NAME: [str(self.gerlach.pk)],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SummitRange.objects.count(), 0)
        self.assertContains(response, 'Assign to range')

    def test_the_confirmation_page_carries_select_across(self):
        """
        Without this the second POST collapses to the visible page, and "select all 1680"
        silently assigns 100 — no error, just most of the work undone.
        """
        response = self.client.post(reverse('admin:mountains_summit_changelist'), {
            'action': 'assign_to_range',
            ACTION_CHECKBOX_NAME: [str(self.gerlach.pk)],
            'select_across': '1',
        })
        self.assertContains(response, 'name="select_across" value="1"')

    def test_select_across_really_reaches_every_summit(self):
        response = self.client.post(reverse('admin:mountains_summit_changelist'), {
            'action': 'assign_to_range',
            ACTION_CHECKBOX_NAME: [str(self.gerlach.pk)],
            'select_across': '1',
            'apply': '1',
            'range': str(self.vysoke.pk),
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SummitRange.objects.count(), 2, 'both summits, not just the ticked one')


class TreeColumnTests(TestCase):
    def test_the_tree_column_indents_by_depth(self):
        """Pure arithmetic on the path; no database, and the place an off-by-one would hide."""
        admin = RangeAdmin(Range, None)
        self.assertNotIn(' ', admin.tree_name(Range(name='Karpaty', path='3.')))
        self.assertIn(' ' * 4, admin.tree_name(Range(name='Tatry', path='3.41.')))
        self.assertIn(' ' * 8, admin.tree_name(Range(name='Vysoké', path='3.41.118.')))


class AdminPagesRenderTests(RangeAdminTestCase):
    """
    `manage.py check` passes over an MRO mistake or a geometry widget that fails to build, so
    the only way to catch those is to open the pages.
    """

    def test_the_range_changelist_renders_the_tree(self):
        response = self.client.get(reverse('admin:mountains_range_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'range-indent')
        self.assertContains(response, 'add child')

    def test_the_range_add_form_renders_its_map(self):
        response = self.client.get(reverse('admin:mountains_range_add'))
        self.assertEqual(response.status_code, 200)

    def test_the_range_change_form_renders(self):
        response = self.client.get(
            reverse('admin:mountains_range_change', args=[self.vysoke.pk]))
        self.assertEqual(response.status_code, 200)

    def test_the_summit_changelist_renders_its_range_column(self):
        assign_range(self.gerlach, self.vysoke)
        response = self.client.get(reverse('admin:mountains_summit_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vysoké Tatry')

    def test_the_unassigned_filter_finds_the_backlog(self):
        assign_range(self.gerlach, self.vysoke)
        response = self.client.get(reverse('admin:mountains_summit_changelist'),
                                   {'assigned': 'no'})
        self.assertEqual(response.status_code, 200)
        listed = response.context['cl'].queryset
        self.assertIn(self.krivan, listed)
        self.assertNotIn(self.gerlach, listed)

    def test_the_range_filter_reaches_through_sub_ranges(self):
        """`?range=` is the link behind a range's summit count, and it must include below."""
        assign_range(self.gerlach, self.vysoke)
        response = self.client.get(reverse('admin:mountains_summit_changelist'),
                                   {'range': str(self.tatry.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.gerlach, response.context['cl'].queryset)

    def test_the_parent_dropdown_is_narrowed_to_one_system(self):
        soiusa = make_range_system('soiusa', 'SOIUSA')
        alpi = make_range(soiusa, 'Alpi')
        response = self.client.get(reverse('admin:mountains_range_add'),
                                   {'system': str(self.system.pk)})
        options = response.context['adminform'].form.fields['parent'].queryset
        self.assertIn(self.tatry, options)
        self.assertNotIn(alpi, options)
