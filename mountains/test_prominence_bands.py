"""
The prominence bands, and the fact that there are two copies of them.

The admin filters by the bands in `mountains/models/summit/prominence.py`; the map colours
by the bands in `static/js/styles.js`. They have to agree, or a curator picking "notable"
in the sidebar gets a different set of summits from the ones painted orange — a discrepancy
nothing would otherwise report, because each side is internally consistent.

So the JS is *run* (see `test_js_runtime`) and read back band for band. A threshold moved on
one side and not the other fails here rather than in someone's eyes six months later.

The rest pins the classification itself: that the seven bands tile the numbers without a gap
or an overlap, where each boundary falls, and that `in_prominence_band()` agrees with
`prominenceBand()` on the awkward values — nothing recorded, exactly on a cutoff, and a
negative prominence, which is a data error rather than a very small summit.
"""

from django.test import SimpleTestCase, TestCase

from mountains.models import Summit
from mountains.models.summit import (PROMINENCE_BANDS, PROMINENCE_BANDS_BY_KEY,
                                     PROMINENCE_UNKNOWN)
from mountains.test_factories import make_col, make_summit
from mountains.test_js_runtime import STYLES_JS, call, evaluate


def js_bands():
    return evaluate('PROMINENCE_BANDS', source=STYLES_JS)


def js_band_of(prominence):
    """`styles.js`'s own classifier, for the value `None` crosses as JSON `null`."""
    return call('prominenceBand', prominence, source=STYLES_JS)['key']


def summit_at(name, prominence):
    """
    A summit of exactly `prominence`, standing on its own key col at sea level.

    Its own col and not a shared one: `key_col` is unique across summits — a saddle is the
    key col of one summit only — so a fixture that reused one would fail on integrity rather
    than on anything this module is about.
    """
    col = make_col(f'{name} col', 0.0, 49.0, 20.0)
    return make_summit(name, prominence, 49.0, 20.0, key_col=col)


class BandsMatchTheMapTests(SimpleTestCase):
    """No database and no Django: two lists of numbers that must be the same list."""

    def test_the_same_bands_in_the_same_order(self):
        self.assertEqual([band['key'] for band in js_bands()],
                         [band.key for band in PROMINENCE_BANDS])

    def test_every_cutoff_is_the_same_number(self):
        self.assertEqual([band['min'] for band in js_bands()],
                         [band.minimum for band in PROMINENCE_BANDS])

    def test_the_wording_is_the_same_too(self):
        """
        Python generates its labels from the cutoffs and JavaScript writes them out, so this
        catches a threshold changed in the ramp without its caption following.
        """
        self.assertEqual([band['label'] for band in js_bands()],
                         [band.label for band in PROMINENCE_BANDS])

    def test_the_unknown_band_matches_as_well(self):
        unknown = evaluate('PROMINENCE_UNKNOWN', source=STYLES_JS)
        self.assertEqual(unknown['key'], PROMINENCE_UNKNOWN.key)
        self.assertEqual(unknown['label'], PROMINENCE_UNKNOWN.label)

    def test_each_band_ends_where_the_next_begins(self):
        """No gap and no overlap, which is what makes the seven a partition."""
        for upper, lower in zip(PROMINENCE_BANDS, PROMINENCE_BANDS[1:]):
            with self.subTest(band=lower.key):
                self.assertEqual(lower.maximum, upper.minimum)
        self.assertIsNone(PROMINENCE_BANDS[0].maximum, 'the top band is open')
        self.assertEqual(PROMINENCE_BANDS[-1].minimum, 0,
                         'the bottom band stops at zero, so a negative value is unknown')


class BandLabelTests(SimpleTestCase):
    def test_the_open_top_band_reads_as_a_floor(self):
        self.assertEqual(PROMINENCE_BANDS_BY_KEY['ultra'].label, 'ultra (≥ 1500 m)')

    def test_a_middle_band_reads_as_a_range(self):
        self.assertEqual(PROMINENCE_BANDS_BY_KEY['modest'].label, 'modest (200–300 m)')

    def test_the_bottom_band_reads_as_a_ceiling_not_as_zero_to_thirty(self):
        self.assertEqual(PROMINENCE_BANDS_BY_KEY['subsidiary'].label, 'subsidiary (< 30 m)')

    def test_the_unknown_band_claims_no_numbers(self):
        self.assertEqual(PROMINENCE_UNKNOWN.label, 'unknown')


class BoundaryTests(TestCase):
    """
    Where each cutoff falls, decided once and checked against both implementations.

    Every summit here is given its own key col at sea level, so its prominence is simply
    its altitude — the arithmetic is not what is under test. Its *own* col, because a col
    is the key col of at most one summit and sharing one raises.
    """

    def band_of(self, prominence):
        summit_at(f'P{prominence}', prominence)
        found = [key for key in PROMINENCE_BANDS_BY_KEY
                 if Summit.objects.in_prominence_band(key).filter(
                     point__altitude=prominence).exists()]
        self.assertEqual(len(found), 1, f'{prominence} m landed in {found}')
        return found[0]

    def test_a_value_on_a_cutoff_belongs_to_the_band_above_it(self):
        self.assertEqual(self.band_of(1500.0), 'ultra')
        self.assertEqual(self.band_of(600.0), 'major')
        self.assertEqual(self.band_of(30.0), 'small')

    def test_a_value_just_under_a_cutoff_belongs_to_the_band_below(self):
        self.assertEqual(self.band_of(1499.9), 'major')
        self.assertEqual(self.band_of(599.9), 'notable')
        self.assertEqual(self.band_of(29.9), 'subsidiary')

    def test_a_flat_summit_is_subsidiary_rather_than_unknown(self):
        self.assertEqual(self.band_of(0.0), 'subsidiary')

    def test_both_implementations_agree_on_every_boundary(self):
        """The point of having the JS runnable: the same values through the other side."""
        for prominence in (8848.86, 1500.0, 1499.9, 600.0, 300.0, 200.0, 199.9,
                           100.0, 30.0, 29.9, 0.0):
            with self.subTest(prominence=prominence):
                self.assertEqual(self.band_of(prominence), js_band_of(prominence))


class UnknownBandTests(TestCase):
    def test_a_summit_with_no_key_col_has_no_band(self):
        make_summit('Unsurveyed', 1200.0, 49.0, 20.0)
        self.assertEqual([s.point.name for s in Summit.objects.in_prominence_band('unknown')],
                         ['Unsurveyed'])

    def test_it_is_not_swept_into_the_bottom_band(self):
        make_summit('Unsurveyed', 1200.0, 49.0, 20.0)
        self.assertFalse(Summit.objects.in_prominence_band('subsidiary').exists())

    def test_a_negative_prominence_is_unknown_and_not_a_tiny_summit(self):
        """
        Impossible terrain — a key col above its summit — and the map reads it as unknown.
        A plain `< 30` would file it under subsidiary and hide the error.
        """
        above = make_col('Impossible', 2000.0, 49.0, 20.0)
        make_summit('Inverted', 1000.0, 49.0, 20.0, key_col=above)
        self.assertTrue(Summit.objects.in_prominence_band('unknown').exists())
        self.assertFalse(Summit.objects.in_prominence_band('subsidiary').exists())
        self.assertEqual(js_band_of(-1000.0), 'unknown')

    def test_an_island_high_point_is_banded_by_its_altitude(self):
        """Its col is the sea, so it has no key col and still has a prominence."""
        make_summit('Island', 800.0, 49.0, 20.0, island_high_point=True)
        self.assertTrue(Summit.objects.in_prominence_band('major').exists())
        self.assertFalse(Summit.objects.in_prominence_band('unknown').exists())


class BandSelectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, prominence in (('Big', 1600.0), ('Middling', 250.0), ('Small', 50.0)):
            summit_at(name, prominence)
        make_summit('Unsurveyed', 900.0, 49.0, 20.0)

    def names(self, key):
        return sorted(s.point.name for s in Summit.objects.in_prominence_band(key))

    def test_every_summit_falls_in_exactly_one_band(self):
        counted = sum(Summit.objects.in_prominence_band(key).count()
                      for key in PROMINENCE_BANDS_BY_KEY)
        self.assertEqual(counted, Summit.objects.count())

    def test_an_unknown_key_selects_nothing_rather_than_everything(self):
        """A stale query string must not quietly show the unfiltered list."""
        self.assertEqual(self.names('enormous'), [])

    def test_it_composes_with_a_queryset_that_already_has_the_annotation(self):
        """`with_prominence()` annotates the same alias; annotating it twice would raise."""
        self.assertEqual(
            sorted(s.point.name for s in
                   Summit.objects.with_prominence().in_prominence_band('modest')),
            ['Middling'])

    def test_it_composes_the_other_way_round_too(self):
        self.assertEqual(
            sorted(s.point.name for s in
                   Summit.objects.in_prominence_band('modest').with_prominence()),
            ['Middling'])

    def test_it_annotates_the_number_it_filtered_on(self):
        """Which is what lets the admin print a prominence column off the same queryset."""
        self.assertEqual(Summit.objects.in_prominence_band('ultra').first().prominence,
                         1600.0)

    def test_filtering_alone_costs_one_query(self):
        """
        It annotates bare rather than calling `with_prominence()`, which would also prefetch
        key cols and prominence parents — two queries for rows a filter is discarding.
        """
        with self.assertNumQueries(1):
            list(Summit.objects.in_prominence_band('ultra'))
