"""
Current and historical names.

A `PointName` is either in use or `historical`. The detail pages list the two apart; a
summit's map popup lists the current ones, the most important language first — Language
priority counts up from the top, English 0 — each with the flag of its language.
"""

import json

from django.test import SimpleTestCase, TestCase

from core.models import Language
from mountains.models import PointName, Summit
from mountains.test_factories import make_summit
from mountains.test_js_runtime import evaluate


def make_language(code, priority, country=None):
    return Language.objects.create(iso639_1=code, iso639_3=code + 'x',
                                   country_code=country or code, name=f'{code} native',
                                   english_name=f'{code} english', priority=priority)


class NamesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.summit = make_summit('Gerlachovský štít', 2655.0, 49.164, 20.134)
        point = cls.summit.point
        polish = make_language('pl', 50)
        slovak = make_language('sk', 1)
        german = make_language('de', 20)
        hungarian = make_language('hu', 75)
        PointName.objects.create(point=point, name='Gerlach', language=polish, local=True)
        PointName.objects.create(point=point, name='Gerlachovský štít', language=slovak)
        PointName.objects.create(point=point, name='Gerlsdorfer Spitze', language=german)
        PointName.objects.create(point=point, name='Štít Františka Jozefa', language=slovak,
                                 historical=True)
        PointName.objects.create(point=point, name='Ferenc József-csúcs', language=hungarian,
                                 historical=True)

    def test_current_and_historical_names_are_apart_local_first_then_by_priority(self):
        point = self.summit.point
        self.assertEqual([n.name for n in point.current_names()],
                         ['Gerlach', 'Gerlachovský štít', 'Gerlsdorfer Spitze'])
        self.assertEqual([n.name for n in point.historical_names()],
                         ['Štít Františka Jozefa', 'Ferenc József-csúcs'])

    def test_the_popup_gets_the_current_names_with_their_flags(self):
        summit = Summit.objects.with_popup().get(pk=self.summit.pk)
        with self.assertNumQueries(0):
            names = summit.to_detail_dict()['names']
        # The local name first, however low its language's priority; the rest by priority.
        self.assertEqual(names, [
            {'name': 'Gerlach', 'flag': 'pl'},
            {'name': 'Gerlachovský štít', 'flag': 'sk'},
            {'name': 'Gerlsdorfer Spitze', 'flag': 'de'},
        ])

    def test_the_pages_order_names_as_the_popup_does(self):
        for url in (self.summit.get_absolute_url(), self.summit.point.get_absolute_url()):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                names = html.split('Historical names')[0].split('Names')[-1]
                order = [names.index(n) for n in
                         ('>Gerlach<', '>Gerlachovský štít<', '>Gerlsdorfer Spitze<')]
                self.assertEqual(order, sorted(order))

    def test_the_summit_page_lists_historical_names_apart(self):
        html = self.client.get(self.summit.get_absolute_url()).content.decode()
        names, historical = html.split('Historical names')
        self.assertIn('Gerlsdorfer Spitze', names.split('Names')[-1])
        self.assertIn('Ferenc József-csúcs', historical)
        self.assertNotIn('Ferenc József-csúcs', names)

    def test_the_point_page_lists_historical_names_apart(self):
        html = self.client.get(self.summit.point.get_absolute_url()).content.decode()
        names, historical = html.split('Historical names')
        self.assertIn('Gerlach', names)
        self.assertIn('Štít Františka Jozefa', historical)
        self.assertNotIn('Štít Františka Jozefa', names)


def popup_html(**properties):
    """
    A summit's popup. `prominenceBand()` lives in styles.js, which is not loaded with
    map.js here, so it is stubbed for the one call.
    """
    return evaluate('''(function () {
        globalThis.prominenceBand = () => ({ colour: '#000', label: 'band' });
        try {
            return popupHtml({ get: key => (%s)[key], getGeometry: () => null });
        } finally {
            delete globalThis.prominenceBand;
        }
    })()''' % json.dumps(properties))


class PopupNamesTests(SimpleTestCase):
    def test_each_current_name_is_a_row_under_its_flag(self):
        html = popup_html(type='summit', pk=1, name='Gerlachovský štít', names=[
            {'name': 'Gerlachovský štít', 'flag': 'sk'},
            {'name': 'Gerlach', 'flag': 'pl'},
        ])
        self.assertIn('<th class="section" rowspan="2">names</th>', html)
        self.assertLess(html.index('Gerlachovský štít</td>'), html.index('Gerlach</td>'))
        self.assertIn('mini/pl.png', html)
        self.assertIn('<th class="flag">', html)

    def test_no_names_no_section(self):
        self.assertNotIn('>names<', popup_html(type='summit', pk=1, name='X', names=[]))
        self.assertNotIn('>names<', popup_html(type='summit', pk=1, name='X'))


class LocalNamesTests(TestCase):
    """The best current name in each local language, for the popup titles."""

    @classmethod
    def setUpTestData(cls):
        cls.summit = make_summit('Ruské sedlo peak', 800.0, 49.1, 22.3)
        point = cls.summit.point
        slovak, ukrainian, polish = (make_language('sk', 1), make_language('uk', 40),
                                     make_language('pl', 50))
        PointName.objects.create(point=point, name='Ruský vrch', language=slovak, local=True)
        PointName.objects.create(point=point, name='Руський верх', language=ukrainian, local=True)
        PointName.objects.create(point=point, name='Руська гора', language=ukrainian, local=True)
        PointName.objects.create(point=point, name='Ruski Wierch', language=polish)
        PointName.objects.create(point=point, name='Ruský štít', language=slovak, local=True,
                                 historical=True)

    def test_one_per_local_language_current_only(self):
        names = self.summit.point.local_names()
        self.assertEqual(len(names), 2)                     # sk, uk; pl is not local
        self.assertEqual(names[0], 'Ruský vrch')            # by language priority
        self.assertIn(names[1], ('Руський верх', 'Руська гора'))
        self.assertNotIn('Ruský štít', names)               # historical

    def test_every_popup_payload_carries_them(self):
        summit = Summit.objects.with_popup().get(pk=self.summit.pk)
        with self.assertNumQueries(0):
            self.assertEqual(len(summit.to_detail_dict()['local_names']), 2)


class PopupTitleTests(SimpleTestCase):
    def test_the_title_follows_the_main_name_with_the_other_local_names(self):
        for kind in ('summit', 'col', 'river'):
            with self.subTest(kind=kind):
                html = popup_html(type=kind, pk=1, name='Ruský vrch',
                                  local_names=['Ruský vrch', 'Руський верх'])
                title = html.split('</h3>')[0]
                self.assertIn('<span class="local-names">Руський верх</span>', title)
                self.assertEqual(title.count('Ruský vrch'), 1)   # not repeated

    def test_a_mouth_title_too(self):
        html = popup_html(type='mouth', name='Soutok', local_names=['Soutok', 'Злиття'],
                          minor={'pk': 1, 'name': 'A'}, major=None, mouth_side=None)
        self.assertIn('<span class="local-names">Злиття</span>', html.split('</h3>')[0])

    def test_no_other_local_names_no_span(self):
        self.assertNotIn('local-names', popup_html(type='summit', pk=1, name='X',
                                                   local_names=['X']))
