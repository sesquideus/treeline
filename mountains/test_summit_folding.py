"""
Folded sections on a summit's page.

The lineages and the slope and horizon rankings show one row — the parent — and fold away
up to 25 more behind a checkbox. The folded rows are `tr.more`, hidden by CSS until the
checkbox in the section is ticked; a section with nothing folded has its switch hidden.
"""

import re

from django.test import SimpleTestCase, TestCase

from mountains.templatetags.tree import LINEAGE_MORE
from mountains.test_factories import make_summit
from mountains.views.summit.summit import RANKED_MORE, parent_first

CHAIN = 30


def section(html, heading):
    """The inside of the `div.foldable` whose heading is `heading`, nested divs and all."""
    for block in html.split('<div class="section foldable">')[1:]:
        if re.search(r'<h3>\s*' + re.escape(heading) + r'\s*</h3>', block):
            depth = 1
            for tag in re.finditer(r'<div\b|</div>', block):
                depth += 1 if tag.group() != '</div>' else -1
                if depth == 0:
                    return block[:tag.start()]
            return block
    raise AssertionError(f'no foldable section headed {heading!r}')


def rows(block):
    """(open, folded) row counts in a section's table body."""
    body = block.split('</thead>')[-1]
    folded = len(re.findall(r'<tr class="more[" ]', body))
    return len(re.findall(r'<tr\b', body)) - folded, folded


class LineageFoldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # A chain longer than the fold, each peak higher than the last, every parent link
        # pointing up it: the page's summit is the bottom one.
        summits = []
        for i in reversed(range(CHAIN)):
            parent = summits[-1] if summits else None
            summits.append(make_summit(f'Step {i:02d}', 1000.0 + 10 * i, 49.0, 20.0 + i / 100,
                                       slope_parent=parent, horizon_parent=parent,
                                       horizon_parent_std=parent))
        cls.bottom = summits[-1]

    def page(self):
        return self.client.get(self.bottom.get_absolute_url()).content.decode()

    def test_a_lineage_shows_its_parent_and_folds_at_most_the_next_25(self):
        self.assertEqual(LINEAGE_MORE, 25)
        html = self.page()
        for heading in ('Slope lineage', 'Horizon lineage',
                        'Horizon lineage (standard refraction)'):
            with self.subTest(heading=heading):
                self.assertEqual(rows(section(html, heading)), (1, 25))

    def test_the_open_row_is_the_parent(self):
        block = section(self.page(), 'Slope lineage')
        first = block.split('<tr')[1]
        self.assertNotIn('class="more"', first.split('>')[0])
        self.assertIn('Step 01', first)

    def test_every_folded_section_has_its_switch(self):
        block = section(self.page(), 'Slope lineage')
        self.assertIn('<label class="fold"><input type="checkbox">', block)


class ShortLineageTests(TestCase):
    def test_a_lineage_of_one_has_nothing_folded(self):
        top = make_summit('Top', 2000.0, 49.0, 20.0)
        low = make_summit('Low', 1000.0, 49.0, 20.1, slope_parent=top)
        html = self.client.get(low.get_absolute_url()).content.decode()
        self.assertEqual(rows(section(html, 'Slope lineage')), (1, 0))


class RankingFoldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.here = make_summit('Here', 1000.0, 49.0, 20.0)
        cls.others = [make_summit(f'Other {i:02d}', 1500.0 + i, 49.0, 20.0 + (i + 1) / 50)
                      for i in range(CHAIN)]

    def test_the_parent_is_open_and_the_next_25_folded(self):
        # Not the top of the ranking: the stored parent is the one shown.
        parent = self.others[10]
        self.here.slope_parent = self.here.horizon_parent = parent
        self.here.save()
        html = self.client.get(self.here.get_absolute_url()).content.decode()
        for heading in ('Horizon angle',):
            with self.subTest(heading=heading):
                block = section(html, heading)
                self.assertEqual(rows(block), (1, RANKED_MORE))
                self.assertIn(parent.point.name, block.split('<tr class="more')[0])

    def test_without_a_parent_the_table_says_so_and_folds_everything(self):
        html = self.client.get(self.here.get_absolute_url()).content.decode()
        block = section(html, 'Horizon angle')
        self.assertIn('no horizon parent', block)
        self.assertEqual(rows(block), (1, RANKED_MORE))       # the one open row is the notice


class Row:
    def __init__(self, pk):
        self.pk = pk


class ParentFirstTests(SimpleTestCase):
    def test_parent_first_then_the_rest_in_order(self):
        result = parent_first([Row(pk) for pk in range(1, 40)], parent_pk=5)
        self.assertEqual([r.pk for r in result][:4], [5, 1, 2, 3])
        self.assertEqual(len(result), 1 + RANKED_MORE)
        self.assertEqual([r.is_parent for r in result[:2]], [True, False])

    def test_no_parent_among_the_rows(self):
        result = parent_first([Row(pk) for pk in range(1, 40)], parent_pk=None)
        self.assertEqual(len(result), RANKED_MORE)
        self.assertFalse(any(r.is_parent for r in result))


class SwitchPlacementTests(TestCase):
    def test_the_switch_sits_beside_the_heading_not_under_the_table(self):
        top = make_summit('Top', 2000.0, 49.0, 20.0)
        low = make_summit('Low', 1000.0, 49.0, 20.1, slope_parent=top)
        block = section(self.client.get(low.get_absolute_url()).content.decode(), 'Slope lineage')
        head = block.split('<table')[0]
        self.assertIn('<div class="fold-head">', head)
        self.assertIn('<label class="fold">', head)
        self.assertNotIn('<label class="fold">', block.split('<table')[1])


class HorizonChildrenFoldTests(TestCase):
    def test_the_children_are_folded_away_entirely_and_counted_on_the_switch(self):
        top = make_summit('Top', 2000.0, 49.0, 20.0)
        for i in range(3):
            make_summit(f'Child {i}', 1000.0 + i, 49.0, 20.1 + i / 100, horizon_parent=top)
        block = section(self.client.get(top.get_absolute_url()).content.decode(),
                        'Horizon children')
        self.assertEqual(rows(block), (0, 3))
        self.assertIn('data-what="3"', block)
