"""
The summit changelist's sidebar.

Two filters, both of them narrowing by something that is not a column on `Summit`.
Prominence is an annotation — the drop to a key col, or an island high point's whole
altitude — and country hangs off the point a summit wraps. Neither can be a plain
`list_filter` string; both are therefore worth a test, because a filter that silently
selects nothing looks exactly like a filter over an empty database.

`mountains/test_prominence_bands.py` pins the bands themselves and their agreement with the
map. What is tested here is the changelist: that the sidebar offers them, that picking one
narrows the list to the right summits, and that a stale query string neither crashes the page
nor quietly shows everything.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from mountains.models.summit import PROMINENCE_BANDS, PROMINENCE_UNKNOWN
from mountains.test_factories import make_col, make_country, make_summit
from users.models import User


class SummitChangelistTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser('curator', 'curator@example.com', 'secret')
        cls.sk = make_country('SK', 'Slovensko')
        cls.pl = make_country('PL', 'Polsko')
        cls.at = make_country('AT', 'Rakúsko')      # no summit sits in it

        # Prominence is altitude minus key col, and each summit needs a col of its own.
        cls.gerlach = cls.summit('Gerlach', 2655.0, 1000.0, [cls.sk])        # 1655 — ultra
        cls.rysy = cls.summit('Rysy', 2503.0, 2250.0, [cls.sk, cls.pl])      #  253 — modest
        cls.bump = cls.summit('Bump', 1200.0, 1190.0, [cls.pl])              #   10 — subsidiary
        # No key col at all, so no prominence to place.
        cls.unsurveyed = make_summit('Unsurveyed', 1400.0, 49.2, 20.2)
        cls.unsurveyed.point.countries.set([cls.sk])

    @classmethod
    def summit(cls, name, altitude, col_altitude, countries):
        col = make_col(f'{name} col', col_altitude, 49.0, 20.0)
        summit = make_summit(name, altitude, 49.1, 20.1, key_col=col)
        summit.point.countries.set(countries)
        return summit

    def setUp(self):
        self.client.force_login(self.user)

    def changelist(self, **params):
        return self.client.get(reverse('admin:mountains_summit_changelist'), params)

    def listed(self, response):
        """The summits on the page, by name."""
        self.assertEqual(response.status_code, 200)
        return sorted(summit.point.name
                      for summit in response.context['cl'].result_list)


class ProminenceFilterTests(SummitChangelistTestCase):
    def sidebar(self):
        # Escaped, because `subsidiary (< 30 m)` reaches the page as `&lt; 30 m`.
        return self.changelist().content.decode()

    def test_the_sidebar_offers_every_band_the_map_paints(self):
        html = self.sidebar()
        for band in (*PROMINENCE_BANDS, PROMINENCE_UNKNOWN):
            with self.subTest(band=band.key):
                self.assertIn(escape(band.label), html)
                self.assertIn(f'?prominence={band.key}', html)

    def test_the_bands_are_listed_from_the_top_down(self):
        """Descending, the way the legend reads — not alphabetically by key."""
        html = self.sidebar()
        positions = [html.index(escape(band.label)) for band in PROMINENCE_BANDS]
        self.assertEqual(positions, sorted(positions))

    def test_picking_a_band_narrows_the_list_to_it(self):
        self.assertEqual(self.listed(self.changelist(prominence='ultra')), ['Gerlach'])
        self.assertEqual(self.listed(self.changelist(prominence='modest')), ['Rysy'])
        self.assertEqual(self.listed(self.changelist(prominence='subsidiary')), ['Bump'])

    def test_a_summit_with_no_key_col_is_reachable_under_unknown(self):
        """The curation backlog: what the sidebar is most useful for."""
        self.assertEqual(self.listed(self.changelist(prominence='unknown')), ['Unsurveyed'])

    def test_no_band_chosen_still_lists_everything(self):
        self.assertEqual(self.listed(self.changelist()),
                         ['Bump', 'Gerlach', 'Rysy', 'Unsurveyed'])

    def test_a_stale_band_shows_nothing_rather_than_everything(self):
        """
        A renamed band left in a bookmark. The page must still render — the admin turns an
        unparseable filter into a redirect — and must not pass off the whole table as a
        filtered view of it.
        """
        response = self.changelist(prominence='enormous')
        self.assertEqual(self.listed(response), [])

    def test_the_filter_survives_being_combined_with_another(self):
        self.assertEqual(
            self.listed(self.changelist(prominence='modest',
                                        **{'point__countries__id__exact': self.pl.pk})),
            ['Rysy'])


class CountryFilterTests(SummitChangelistTestCase):
    def parameter(self, country):
        return {'point__countries__id__exact': str(country.pk)}

    def test_picking_a_country_narrows_the_list_to_it(self):
        self.assertEqual(self.listed(self.changelist(**self.parameter(self.pl))),
                         ['Bump', 'Rysy'])

    def test_a_summit_on_a_border_appears_under_both_its_countries(self):
        self.assertIn('Rysy', self.listed(self.changelist(**self.parameter(self.sk))))
        self.assertIn('Rysy', self.listed(self.changelist(**self.parameter(self.pl))))

    def test_a_border_summit_is_listed_once_under_each(self):
        """A join across a many-to-many is a standing invitation to duplicate rows."""
        listed = self.listed(self.changelist(**self.parameter(self.sk)))
        self.assertEqual(listed, sorted(set(listed)))

    def test_the_sidebar_offers_the_countries_that_have_summits(self):
        html = self.changelist().content.decode()
        self.assertIn(str(self.sk), html)
        self.assertIn(str(self.pl), html)

    def test_a_country_with_no_summits_is_not_offered(self):
        """`RelatedOnly`: a country nothing sits in is a link to an empty page."""
        self.assertNotIn(str(self.at), self.changelist().content.decode())
