"""
Finding a col that has no name.

A new col is usually unnamed and nobody's key col yet, so a search by name has nothing to
match — and the key-col box on a summit searches the col admin, so such a col could not be
attached to anything. These go through the autocomplete endpoint that box actually calls.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from mountains.test_factories import make_col, make_summit


class UnnamedColSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.unnamed = make_col(None, 943.4, 49.10, 20.10)
        cls.other = make_col(None, 943.96, 49.20, 20.20)
        cls.named = make_col('Przełęcz Brona', 1408.0, 49.58, 19.50)
        cls.below = make_col(None, 942.95, 49.30, 20.30)
        cls.summit = make_summit('Babia Góra', 1724.0, 49.57, 19.53)
        cls.admin = get_user_model().objects.create_superuser('admin', password='x')

    def setUp(self):
        self.client.force_login(self.admin)

    def search(self, term):
        response = self.client.get(reverse('admin:autocomplete'), {
            'app_label': 'mountains', 'model_name': 'summit', 'field_name': 'key_col',
            'term': term})
        self.assertEqual(response.status_code, 200)
        return {int(result['id']) for result in response.json()['results']}

    def test_a_whole_number_is_the_metre_as_typed(self):
        self.assertEqual(self.search('943'), {self.unnamed.pk, self.other.pk} | self.pks(943))

    def test_a_decimal_is_that_decimetre(self):
        self.assertEqual(self.search('943.4'), {self.unnamed.pk})

    def test_a_whole_number_also_finds_the_pk(self):
        self.assertIn(self.named.pk, self.search(str(self.named.pk)))

    def test_unnamed_lists_the_nameless(self):
        self.assertEqual(self.search('unnamed'), {self.unnamed.pk, self.other.pk, self.below.pk})

    def test_a_name_still_works(self):
        self.assertEqual(self.search('brona'), {self.named.pk})

    def pks(self, number):
        """The pk match a number makes as well, should a col happen to have that pk."""
        return {pk for pk in (self.unnamed.pk, self.other.pk, self.named.pk, self.below.pk)
                if pk == number}
