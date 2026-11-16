"""
The mountain list, a hundred to a page.

Paging only works over a total order: the list sorts on columns full of ties (altitudes
given to the metre, the summits with no isolation at all), and without a last tiebreak the
database may break them differently from one request to the next — so a summit could show
up on two pages and another on none. The fixture is all ties on purpose.
"""

import re

from django.test import TestCase
from django.urls import reverse

from mountains.test_factories import make_summit
from mountains.views.summit.list import MountainListView

PER_PAGE = MountainListView.paginate_by


class PaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # One altitude for all, so every page boundary falls inside a tie.
        cls.summits = [make_summit(f'Peak {i:03d}', 2000.0, 49.0, 20.0 + i / 1000)
                       for i in range(PER_PAGE + 5)]

    def page(self, **params):
        response = self.client.get(reverse('mountain-list'), params)
        self.assertEqual(response.status_code, 200)
        return response

    def names(self, response):
        return [m.point.name for m in response.context['mountains']]

    def test_a_hundred_to_a_page(self):
        self.assertEqual(PER_PAGE, 100)
        self.assertEqual(len(self.names(self.page())), 100)
        self.assertEqual(len(self.names(self.page(page=2))), 5)

    def test_the_pages_cover_every_summit_once_even_through_ties(self):
        for ordering in ('', 'altitude', '-altitude', 'isolation', '-prominence'):
            with self.subTest(ordering=ordering):
                seen = (self.names(self.page(ordering=ordering))
                        + self.names(self.page(ordering=ordering, page=2)))
                self.assertEqual(sorted(seen), sorted(s.point.name for s in self.summits))

    def test_rows_are_numbered_through_the_whole_list(self):
        html = self.page(page=2).content.decode()
        numbers = re.findall(r'<td class="number">(\d+)</td>', html)
        self.assertEqual(numbers[:5], ['101', '102', '103', '104', '105'])

    def test_page_links_keep_the_ordering_and_the_filter(self):
        html = self.page(ordering='-name', name='Peak').content.decode()
        self.assertIn('href="?ordering=-name&amp;name=Peak&amp;page=2"', html)
        self.assertIn('101&ndash;105 of 105', self.page(page=2).content.decode())

    def test_every_page_is_listed_and_the_current_one_marked(self):
        html = self.page(page=2).content.decode()
        self.assertIn('<li><a href="?page=1">1</a></li>', html)
        self.assertIn('<li class="current" aria-current="page">2</li>', html)
        self.assertNotIn('href="?page=2">2<', html)

    def test_a_list_of_one_page_has_no_page_links(self):
        html = self.page(name='Peak 001').content.decode()
        self.assertNotIn('class="pagination"', html)

    def test_a_page_past_the_end_is_not_found(self):
        response = self.client.get(reverse('mountain-list'), {'page': 3})
        self.assertEqual(response.status_code, 404)

    def test_the_last_page_by_name(self):
        self.assertEqual(len(self.names(self.page(page='last'))), 5)


class PageLinksTests(TestCase):
    """`page_links`: every page up to forty, then elided to at most forty entries."""

    def links(self, pages, current):
        from django.core.paginator import Paginator
        from core.templatetags.pagination import page_links
        page = Paginator(range(pages), 1).page(current)
        return list(page_links(page))

    def test_up_to_forty_pages_all_are_listed(self):
        self.assertEqual(self.links(40, 17), list(range(1, 41)))

    def test_past_forty_the_list_is_elided_but_keeps_both_ends_and_the_current(self):
        for current in (1, 60, 120):
            with self.subTest(current=current):
                links = self.links(120, current)
                self.assertLessEqual(len(links), 40)
                self.assertEqual((links[0], links[-1]), (1, 120))
                self.assertIn(current, links)
