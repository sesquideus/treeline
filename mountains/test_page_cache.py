"""
Keeping a crawler from rendering the lists over and over.

The summit list is 4.6 MB of HTML that takes about two seconds to render, nearly all of it
in the template rather than the database; the col and river lists and the forests are of the
same kind. Every column header is a sort link and the sorts combine, so to a crawler the
lists are thousands of distinct URLs at two seconds each — and uwsgi has two workers.

Two defences, tested here:

* `robots.txt` shuts off the arrangements — query strings, compare pairs, the map's JSON —
  while leaving every page and every list in its default order readable.
* The slow pages are cached whole, keyed on their URL and the data version, so a page
  anybody has already asked for costs a few milliseconds.

What a cache can get wrong is serving something stale, so most of what follows is about
when it must *not* answer.
"""

import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.migrations.state import ProjectState
from django.apps import apps
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.views import View

from mountains import views
from mountains.models import Summit
from mountains.signals import is_watched
from mountains.test_factories import make_country, make_summit
from mountains.views.cache import CachedPageMixin, data_version


def robots_rules(text):
    """The `Disallow` paths of the `User-agent: *` group."""
    return [line.split(':', 1)[1].strip() for line in text.splitlines()
            if line.lower().startswith('disallow:')]


def blocked(path, rules):
    """
    Whether `path` is disallowed, read the way the major crawlers read it: a rule is a
    prefix, `*` matches any run of characters, and `$` would anchor the end.
    """
    for rule in rules:
        pattern = '.*'.join(map(re.escape, rule.rstrip('$').split('*')))
        if re.match(pattern + ('$' if rule.endswith('$') else ''), path):
            return True
    return False


class RobotsTests(TestCase):
    def setUp(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.response = response
        self.rules = robots_rules(response.content.decode())

    def test_it_is_plain_text(self):
        self.assertEqual(self.response['Content-Type'], 'text/plain')

    def test_the_pages_and_the_default_lists_stay_open(self):
        for path in ['/', '/cols', '/rivers', '/ranges', '/statistics', '/prominence-tree',
                     '/horizon-forest', '/river-tree', '/map', '/summit/91/', '/col/3/',
                     '/river/12/', '/range/4/', '/point/7/']:
            with self.subTest(path=path):
                self.assertFalse(blocked(path, self.rules))

    def test_the_arrangements_are_shut(self):
        for path in ['/?ordering=prominence', '/?ordering=-isolation,name&name=kri',
                     '/cols?ordering=depth', '/rivers?page=2',
                     '/summit/compare/58/91/', '/summit/compare/?summit1=Rysy',
                     '/summits/geo.json', '/cols/geo.json/', '/summit/91/geo.json/',
                     '/summit/91/prominence-lineage.json', '/summit/search.json?q=kr',
                     '/admin/']:
            with self.subTest(path=path):
                self.assertTrue(blocked(path, self.rules))

    def test_the_matcher_reads_wildcards(self):
        """The reading above, on rules whose meaning is not in doubt."""
        self.assertTrue(blocked('/a/b.json', ['/*.json']))
        self.assertFalse(blocked('/a/b', ['/*.json']))
        self.assertTrue(blocked('/x?y=1', ['/*?']))
        self.assertFalse(blocked('/x', ['/*?']))


@override_settings(PAGE_CACHE=True)
class PageCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.rysy = make_summit('Rysy', 2500, 49.18, 20.09)

    def queries(self, path, **kwargs):
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(path, **kwargs)
        self.assertEqual(response.status_code, 200)
        return response, len(captured)

    def test_a_page_asked_for_twice_is_rendered_once(self):
        first, rendering = self.queries('/')
        second, serving = self.queries('/')
        self.assertGreater(rendering, 0)
        self.assertEqual(serving, 0)
        self.assertEqual(first.content, second.content)

    def test_the_cached_copy_keeps_its_content_type(self):
        self.queries('/')
        response, _ = self.queries('/')
        self.assertEqual(response['Content-Type'], 'text/html; charset=utf-8')

    def test_every_slow_page_is_cached(self):
        for path in ['/', '/cols', '/rivers', '/prominence-tree', '/isolation-tree',
                     '/slope-tree', '/horizon-forest', '/river-tree']:
            with self.subTest(path=path):
                self.queries(path)
                self.assertEqual(self.queries(path)[1], 0)

    def test_a_save_shows_on_the_next_request(self):
        self.queries('/')
        self.rysy.point.name = 'Rysy (new name)'
        self.rysy.point.save()
        response, rendering = self.queries('/')
        self.assertGreater(rendering, 0)
        self.assertContains(response, 'Rysy (new name)')

    def test_a_new_summit_shows_on_the_next_request(self):
        self.queries('/')
        make_summit('Gerlach', 2655, 49.16, 20.13)
        self.assertContains(self.queries('/')[0], 'Gerlach')

    def test_a_delete_shows_on_the_next_request(self):
        doomed = make_summit('Doomed peak', 1000, 49.0, 20.0)
        self.assertContains(self.queries('/')[0], 'Doomed peak')
        doomed.delete()
        self.assertNotContains(self.queries('/')[0], 'Doomed peak')

    def test_a_country_added_to_a_point_invalidates(self):
        """A many-to-many edit is a write too; the admin saves it after the point itself."""
        slovakia = make_country('SK')     # a save of its own, so made before measuring
        before = data_version()
        self.rysy.point.countries.add(slovakia)
        self.assertNotEqual(data_version(), before)

    def test_each_query_string_is_its_own_page(self):
        make_summit('Gerlach', 2655, 49.16, 20.13)
        forwards = self.queries('/?ordering=name')[0].content.decode()
        backwards = self.queries('/?ordering=-name')[0].content.decode()
        self.assertLess(forwards.index('>Gerlach<'), forwards.index('>Rysy<'))
        self.assertGreater(backwards.index('>Gerlach<'), backwards.index('>Rysy<'))

    def test_a_signed_in_user_always_gets_a_fresh_render(self):
        user = get_user_model().objects.create_user('editor', password='x')
        self.client.force_login(user)
        self.queries('/')
        self.assertGreater(self.queries('/')[1], 0)

    def test_a_signed_in_render_is_not_handed_to_anyone_else(self):
        user = get_user_model().objects.create_user('editor', password='x')
        self.client.force_login(user)
        self.queries('/')
        self.client.logout()
        self.assertGreater(self.queries('/')[1], 0)

    def test_a_restart_does_not_serve_what_the_old_code_rendered(self):
        """The cache is on disk and outlives a deploy; the per-start token is what keys it."""
        self.queries('/')
        with mock.patch('mountains.views.cache.BOOT', 'restarted'):
            self.assertGreater(self.queries('/')[1], 0)

    @override_settings(PAGE_CACHE=False)
    def test_it_can_be_switched_off(self):
        self.queries('/')
        self.assertGreater(self.queries('/')[1], 0)


class CountingView(CachedPageMixin, View):
    """Answers with whatever `status` says, and counts how often it really ran."""
    calls = 0
    status = 200

    def get(self, request):
        type(self).calls += 1
        return HttpResponse(f'call {type(self).calls}', status=type(self).status)


@override_settings(PAGE_CACHE=True)
class WhatIsNotStoredTests(TestCase):
    def setUp(self):
        cache.clear()
        CountingView.calls, CountingView.status = 0, 200
        self.factory = RequestFactory()

    def call(self, method='get', path='/counting'):
        request = getattr(self.factory, method)(path)
        request.user = mock.Mock(is_authenticated=False)
        return CountingView.as_view()(request)

    def test_an_error_is_not_kept(self):
        CountingView.status = 500
        self.call()
        CountingView.status = 200
        self.assertEqual(self.call().content, b'call 2')

    def test_a_post_is_never_answered_from_the_cache(self):
        self.call()
        CountingView.post = CountingView.get
        try:
            self.assertEqual(self.call('post').content, b'call 2')
        finally:
            del CountingView.post

    def test_a_good_answer_is_kept(self):
        self.call()
        self.assertEqual(self.call().content, b'call 1')


class InvalidationScopeTests(SimpleTestCase):
    def test_a_migrations_historical_model_still_invalidates(self):
        """A data migration writes through these, not the real classes."""
        historical = ProjectState.from_apps(apps).apps.get_model('mountains', 'Summit')
        self.assertIsNot(historical, Summit)
        self.assertTrue(is_watched(historical))

    def test_everything_the_pages_draw_on_is_watched(self):
        for label in ['mountains.NamedPoint', 'mountains.Summit', 'mountains.Col',
                      'mountains.River', 'mountains.Range', 'mountains.SummitRange',
                      'mountains.PointName', 'core.Country']:
            with self.subTest(model=label):
                self.assertTrue(is_watched(apps.get_model(label)))

    def test_a_login_does_not_invalidate(self):
        """`last_login` is saved on every sign-in; it has nothing to do with the pages."""
        self.assertFalse(is_watched(get_user_model()))
        self.assertFalse(is_watched(apps.get_model('sessions', 'Session')))

    def test_the_slow_views_are_the_cached_ones(self):
        for view in [views.summit.MountainListView, views.col.ListView, views.river.ListView,
                     views.river.RiverForestView, views.ProminenceForestView,
                     views.IsolationForestView, views.SlopeTreeView, views.HorizonTreeView]:
            with self.subTest(view=view.__name__):
                self.assertTrue(issubclass(view, CachedPageMixin))
