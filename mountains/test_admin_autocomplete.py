"""
Relations into the big tables go through autocomplete in the admin.

A plain select renders every row as an <option>: with seven thousand named points and a
thousand rivers, a col's change page carried both lists whole. Range `parent` is not here
on purpose — it is a dropdown narrowed to the range's system and indented as a tree.
"""

from django.contrib import admin
from django.contrib.admin.widgets import AutocompleteSelect
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from mountains.models import Col, NamedPoint, Note, PointName, River, Summit

BIG_RELATIONS = {
    Summit: ['point', 'key_col', 'prominence_parent', 'isolation_parent', 'slope_parent',
             'horizon_parent'],
    River: ['source', 'source_summit', 'watershed_high_point', 'branches_off', 'mouth',
            'parent'],
    Col: ['point', 'confluence_river'],
    Note: ['point'],
    PointName: ['point'],
}


class AutocompleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('admin', password='x')
        self.request = RequestFactory().get('/admin/')
        self.request.user = self.user

    def test_every_big_relation_is_an_autocomplete(self):
        for model, fields in BIG_RELATIONS.items():
            form = admin.site._registry[model].get_form(self.request)
            for name in fields:
                with self.subTest(model=model.__name__, field=name):
                    widget = form.base_fields[name].widget
                    self.assertIsInstance(getattr(widget, 'widget', widget), AutocompleteSelect)

    def test_the_add_pages_render(self):
        self.client.force_login(self.user)
        for model in BIG_RELATIONS:
            with self.subTest(model=model.__name__):
                url = reverse(f'admin:mountains_{model._meta.model_name}_add')
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_col_page_no_longer_lists_every_point(self):
        for i in range(30):
            NamedPoint.objects.create(name=f'Filler {i}', altitude=1000 + i,
                                      location='POINT(20 49)')
        self.client.force_login(self.user)
        page = self.client.get(reverse('admin:mountains_col_add')).content.decode()
        self.assertNotIn('Filler 7', page)
