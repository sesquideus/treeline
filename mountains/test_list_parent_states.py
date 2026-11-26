"""
The mountain list tells a horizon parent that does not exist (computed, none found) apart from one
never computed: "none" against "?".
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from mountains.models import Summit
from mountains.test_factories import make_summit


class HorizonParentStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.high = make_summit('Vysoká', 2000.0, 49.0, 20.0)
        cls.child = make_summit('Nižná', 1500.0, 49.01, 20.01)
        cls.lone = make_summit('Osamelá', 1200.0, 48.5, 19.5)
        cls.unknown = make_summit('Neznáma', 1100.0, 48.6, 19.6)
        Summit.objects.filter(pk=cls.child.pk).update(horizon_parent=cls.high, horizon_computed=timezone.now())
        Summit.objects.filter(pk=cls.lone.pk).update(horizon_computed=timezone.now())

    def test_the_three_states(self):
        html = self.client.get(reverse('mountain-list')).content.decode()
        row = lambda name: html[html.index(name):html.index('</tr>', html.index(name))]
        self.assertIn('Vysoká', row('Nižná'))                     # a parent: its link
        self.assertIn('class="parent-none"', row('Osamelá'))      # computed, none exists
        self.assertNotIn('parent-unknown', row('Osamelá'))
        self.assertIn('class="parent-unknown"', row('Neznáma'))   # never computed
        # no parent, no distance: a grey dash, not a question mark, in both cases
        for name in ('Osamelá', 'Neznáma'):
            self.assertIn('<span class="parent-none">&mdash;</span></td>', row(name))
