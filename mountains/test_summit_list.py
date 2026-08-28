"""
Tests for the slope-parent and horizon-parent columns of the mountain list.

The three columns under test — distance to slope parent, height difference to slope
parent, and distance to horizon parent — are all queryset annotations, so each is
checked twice: once against the pure-Python helper on `NamedPoint` that computes the
same quantity, and once through the rendered list page and its ordering keys.
"""

import re

from django.test import TestCase
from django.urls import reverse

from mountains.models import Summit
from mountains.test_factories import make_summit


class ParentColumnTestCase(TestCase):
    """
    Three summits in a row along the 49th parallel, all hanging off the highest one:

        alpha (3000 m, 20.0°E)  ---  beta (2000 m, 20.1°E)  ---  gamma (1000 m, 20.5°E)

    `alpha` has no slope or horizon parent, so its annotations must stay NULL; `beta` is
    the near child and `gamma` the far one, which pins down every ordering below.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alpha = make_summit('Alpha', 3000.0, 49.0, 20.0)
        cls.beta = make_summit('Beta', 2000.0, 49.0, 20.1,
                               slope_parent=cls.alpha, horizon_parent=cls.alpha)
        cls.gamma = make_summit('Gamma', 1000.0, 49.0, 20.5,
                                slope_parent=cls.alpha, horizon_parent=cls.alpha)

    def annotated(self, summit):
        return (Summit.objects
                .with_point()
                .with_slope_parent()
                .with_horizon_parent()
                .get(pk=summit.pk))


class SlopeParentAnnotationTests(ParentColumnTestCase):
    def test_dh_is_the_ascent_to_the_slope_parent(self):
        """`dh` is signed parent-minus-self, so it is positive towards higher ground."""
        self.assertAlmostEqual(self.annotated(self.beta).dh, 1000.0, places=6)
        self.assertAlmostEqual(self.annotated(self.gamma).dh, 2000.0, places=6)

    def test_dh_matches_the_model_helper(self):
        beta = self.annotated(self.beta)
        self.assertAlmostEqual(beta.dh, self.beta.ascent_to_slope_parent(), places=6)

    def test_dd_is_the_distance_to_the_slope_parent(self):
        for summit in (self.beta, self.gamma):
            with self.subTest(summit=summit.point.name):
                expected_km = summit.point.distance_to(self.alpha.point)
                self.assertAlmostEqual(self.annotated(summit).dd.km, expected_km, places=2)

    def test_slope_matches_the_python_helper(self):
        for summit in (self.beta, self.gamma):
            with self.subTest(summit=summit.point.name):
                expected = summit.point.slope_to(self.alpha.point)
                self.assertAlmostEqual(self.annotated(summit).slope, expected, places=6)

    def test_slope_annotation_is_dh_over_dd(self):
        beta = self.annotated(self.beta)
        self.assertAlmostEqual(beta.slope, beta.dh / beta.dd.m, places=9)

    def test_without_a_slope_parent_the_annotations_are_null(self):
        alpha = self.annotated(self.alpha)
        self.assertIsNone(alpha.dh)
        self.assertIsNone(alpha.dd)
        self.assertIsNone(alpha.slope)


class HorizonParentAnnotationTests(ParentColumnTestCase):
    def test_distance_to_horizon_matches_the_python_helper(self):
        for summit in (self.beta, self.gamma):
            with self.subTest(summit=summit.point.name):
                expected_km = summit.point.distance_to(self.alpha.point)
                self.assertAlmostEqual(
                    self.annotated(summit).distance_to_horizon.km, expected_km, places=2
                )

    def test_angle_still_matches_the_python_helper(self):
        """
        `beta` (and so `angle`) is derived from the `distance_to_horizon` annotation;
        this guards the angle against a regression in that derivation.
        """
        for summit in (self.beta, self.gamma):
            with self.subTest(summit=summit.point.name):
                expected = summit.point.angle_to(self.alpha.point)
                self.assertAlmostEqual(self.annotated(summit).angle, expected, delta=1e-7)

    def test_annotation_does_not_shadow_the_model_method(self):
        """
        `Summit.distance_to_horizon_parent()` returns kilometres; the annotation is a
        `Distance`. They must stay separately addressable, or templates calling the
        method silently start receiving a measure object instead.
        """
        beta = self.annotated(self.beta)
        self.assertAlmostEqual(
            beta.distance_to_horizon_parent(), beta.distance_to_horizon.km, places=2
        )

    def test_without_a_horizon_parent_the_annotations_are_null(self):
        alpha = self.annotated(self.alpha)
        self.assertIsNone(alpha.distance_to_horizon)
        self.assertIsNone(alpha.angle)


class MountainListColumnTests(ParentColumnTestCase):
    def get_list(self, ordering=None):
        url = reverse('mountain-list')
        response = self.client.get(url, {'ordering': ordering} if ordering else {})
        self.assertEqual(response.status_code, 200)
        return response

    def names_in_order(self, ordering):
        return [
            mountain.point.name
            for mountain in self.get_list(ordering).context['mountains']
        ]

    def header_rows(self):
        """The grouping row and the per-column row of `<thead>`, as raw HTML."""
        html = self.get_list().content.decode()
        head = html.split('<thead>')[1].split('</thead>')[0]
        group, _, columns = head.partition('<tr class="subheader">')
        return group, columns

    def test_every_header_has_a_matching_cell(self):
        """A column added to one of thead/tbody but not the other skews the whole table."""
        _, columns = self.header_rows()
        html = self.get_list().content.decode()
        first_row = html.split('<tbody>')[1].split('</tr>')[0]
        self.assertEqual(columns.count('<th'), first_row.count('<td'))

    def test_the_group_header_spans_every_column(self):
        """The colspans of the grouping row have to add up, or the groups shear sideways."""
        group, columns = self.header_rows()
        spans = [int(match) for match in re.findall(r'<th colspan="(\d+)"', group)]
        ungrouped = group.count('<th') - len(spans)
        self.assertEqual(sum(spans) + ungrouped, columns.count('<th'))

    def test_the_new_columns_are_labelled(self):
        """
        The visible labels are group-relative ("distance"), so the tooltips are what
        actually identify these three columns to a reader.
        """
        html = self.get_list().content.decode()
        for tooltip in ('distance to slope parent',
                        'height difference to slope parent',
                        'distance to horizon parent'):
            with self.subTest(tooltip=tooltip):
                self.assertIn(f'<abbr title="{tooltip}">', html)

    def test_the_new_cells_show_the_annotated_values(self):
        """Distances render in kilometres, the height difference in signed metres."""
        html = self.get_list('slope-parent-alt-diff').content.decode()
        beta_row = html.split('<tbody>')[1].split('</tbody>')[0].split('<tr>')[1]
        distance_km = f'{self.beta.point.distance_to(self.alpha.point):.3f}'
        self.assertEqual(beta_row.count(f'<td class="distance">{distance_km}</td>'), 2)
        self.assertIn('<td class="altitude">+1000.0</td>', beta_row)

    def test_ordering_by_distance_to_slope_parent(self):
        self.assertEqual(self.names_in_order('slope-parent-dist'), ['Beta', 'Gamma', 'Alpha'])
        self.assertEqual(self.names_in_order('-slope-parent-dist'), ['Gamma', 'Beta', 'Alpha'])

    def test_ordering_by_height_difference_to_slope_parent(self):
        self.assertEqual(self.names_in_order('slope-parent-alt-diff'), ['Beta', 'Gamma', 'Alpha'])
        self.assertEqual(self.names_in_order('-slope-parent-alt-diff'), ['Gamma', 'Beta', 'Alpha'])

    def test_ordering_by_distance_to_horizon_parent(self):
        self.assertEqual(self.names_in_order('horizon-parent-dist'), ['Beta', 'Gamma', 'Alpha'])
        self.assertEqual(self.names_in_order('-horizon-parent-dist'), ['Gamma', 'Beta', 'Alpha'])
