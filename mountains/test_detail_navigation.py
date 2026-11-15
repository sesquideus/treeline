"""
Moving between the summit, col and river pages without reloading — `static/js/summit-detail-map.js`.

A link to another detail page, in the data pane or a map popup, and a click on a mark,
fetch that page and swap in only its data pane, title, map controls and map features. The
URL stays the real `/summit/<pk>/`, pushed with the History API, so a reload or a shared
link gets the same page from the server; anything that goes wrong falls back to an
ordinary navigation.

Run for real in QuickJS against stand-ins for the DOM, `fetch` and `history`: a fresh
engine per test, since every test rewires the globals, and with the promise jobs drained by
hand — QuickJS runs them only when asked.
"""

import json

import quickjs
from django.test import SimpleTestCase, TestCase

from mountains.test_factories import make_col, make_summit
from mountains.test_js_runtime import STUBS

DETAIL_MAP_JS = 'static/js/summit-detail-map.js'

#: The page the visit is on, and everything `navigateTo()` touches, recorded rather than
#: done. `pages` maps a path to what `fetch` answers with: [status, title, has controls,
#: has map data]; the answer is built into the shape `DOMParser` would give.
HARNESS = r"""
var window = { location: { href: '', origin: 'https://treeline.test' },
               scrollTo: () => { log.push('scroll'); } };
var log = [], pushed = [], pages = {}, features = null, fitted = 0, popupCleared = 0;
var title = 'Summit one | TreeLine';
var controls = { id: 'map-controls', remove: () => { log.push('controls removed'); controls = null; } };
var dataPane = { replaceWith: node => { log.push('data ' + node.name); } };
var mapElement = { after: node => { log.push('controls added'); controls = node; } };
var document = {
    get title() { return title; }, set title(v) { title = v; },
    getElementById: id => id === 'map-controls' ? controls : id === 'map' ? mapElement : null,
    querySelector: selector => selector === '.detail-page .data' ? dataPane : null,
    addEventListener: () => {},
};
var history = { pushState: (state, unused, path) => { pushed.push(path); },
                replaceState: () => {} };
var navigatedAway = null;
Object.defineProperty(window.location, 'href', {
    get: () => 'https://treeline.test/summit/1/',
    set: value => { navigatedAway = value; },
});
function fetch(path) {
    const page = pages[path];
    if (!page) return Promise.reject(new Error('offline'));
    const [status, pageTitle, withControls, withData] = page;
    return Promise.resolve({ ok: status === 200, status,
                             text: () => Promise.resolve(JSON.stringify(page)) });
}
function DOMParser() {}
DOMParser.prototype.parseFromString = function (text) {
    const [status, pageTitle, withControls, withData] = JSON.parse(text);
    return {
        title: pageTitle,
        querySelector: s => s === '.detail-page .data' ? { name: pageTitle } : null,
        getElementById: id =>
            id === 'map-controls' ? (withControls ? { id: 'map-controls', from: pageTitle } : null)
            : id === 'map-data' && withData ? { textContent: JSON.stringify({ page: pageTitle }) }
            : null,
    };
};
function fitToFramed() { fitted++; }
"""

#: What the map holds once `initDetailMap()` has run: a source that records what it is
#: given, so a swap can be seen to replace the features rather than add to them.
LIVE_MAP = r"""
detailMap = {
    map: {},
    clearPopup: () => { popupCleared++; },
    source: {
        clear: () => { features = []; },
        addFeatures: added => { features = features.concat(added); },
        getFeatures: () => features || [],
    },
};
readFeatures = geojson => [{ page: geojson.page, get: () => null }];
"""


class Engine:
    """One QuickJS context with the script loaded against the harness."""

    def __init__(self, live=True):
        self.context = quickjs.Context()
        self.context.eval(STUBS)
        self.context.eval(HARNESS)
        with open(DETAIL_MAP_JS, encoding='utf-8') as handle:
            self.context.eval(handle.read())
        if live:
            self.context.eval(LIVE_MAP)

    def run(self, source):
        self.context.eval(source)
        while self.context.execute_pending_job():
            pass

    def get(self, expression):
        return json.loads(self.context.eval(f'JSON.stringify({expression}) ?? "null"'))

    def serve(self, path, status=200, title='Page', controls=True, data=True):
        self.run(f'pages[{json.dumps(path)}] = {json.dumps([status, title, controls, data])};')


class PathTests(SimpleTestCase):
    def setUp(self):
        self.engine = Engine(live=False)

    def path(self, href):
        return self.engine.get(f'detailPath({json.dumps(href)}, "https://treeline.test")')

    def test_the_three_kinds_of_detail_page_are_swappable(self):
        for href in ['/summit/12/', '/col/3/', '/river/77/',
                     'https://treeline.test/summit/12/']:
            with self.subTest(href=href):
                self.assertEqual(self.path(href), href.removeprefix('https://treeline.test'))

    def test_everything_else_is_left_to_the_browser(self):
        for href in ['/summit/compare/1/2/', '/point/4/', '/range/2/', '/', '/summit/12/?x=1',
                     '/summit/12/#names', 'https://elsewhere.test/summit/12/',
                     '/admin/mountains/summit/12/change/', '/summit/12']:
            with self.subTest(href=href):
                self.assertIsNone(self.path(href))


class NavigationTests(SimpleTestCase):
    def setUp(self):
        self.engine = Engine()

    def test_a_detail_page_is_swapped_in_place(self):
        self.engine.serve('/col/5/', title='Col five | TreeLine')
        self.engine.run('navigateTo("/col/5/");')
        self.assertEqual(self.engine.get('log'), ['data Col five | TreeLine', 'scroll'])
        self.assertEqual(self.engine.get('title'), 'Col five | TreeLine')
        self.assertEqual(self.engine.get('pushed'), ['/col/5/'])
        self.assertIsNone(self.engine.get('navigatedAway'))

    def test_the_map_gets_the_new_features_instead_of_extra_ones(self):
        self.engine.run('features = [{ page: "old", get: () => null }];')
        self.engine.serve('/col/5/', title='Col five')
        self.engine.run('navigateTo("/col/5/");')
        self.assertEqual(self.engine.get('features.map(f => f.page)'), ['Col five'])
        self.assertEqual(self.engine.get('[fitted, popupCleared]'), [1, 1])
        self.assertIsNone(self.engine.get('navigatedAway'))

    def test_back_and_forward_swap_without_pushing_another_entry(self):
        self.engine.serve('/summit/2/')
        self.engine.run('navigateTo("/summit/2/", false);')
        self.assertEqual(self.engine.get('pushed'), [])
        self.assertEqual(self.engine.get('fitted'), 1)
        self.assertIsNone(self.engine.get('navigatedAway'))

    def test_an_error_page_falls_back_to_an_ordinary_navigation(self):
        self.engine.serve('/summit/404/', status=404)
        self.engine.run('navigateTo("/summit/404/");')
        self.assertEqual(self.engine.get('navigatedAway'), '/summit/404/')
        self.assertEqual(self.engine.get('pushed'), [])

    def test_a_page_without_map_data_falls_back_too(self):
        self.engine.serve('/summit/3/', data=False)
        self.engine.run('navigateTo("/summit/3/");')
        self.assertEqual(self.engine.get('navigatedAway'), '/summit/3/')
        self.assertEqual(self.engine.get('log'), [])

    def test_a_network_failure_falls_back_too(self):
        self.engine.run('navigateTo("/summit/9/");')
        self.assertEqual(self.engine.get('navigatedAway'), '/summit/9/')

    def test_a_late_answer_to_an_earlier_click_is_dropped(self):
        """Two clicks in quick succession: the page shown is the second, whatever the order."""
        self.engine.serve('/summit/2/', title='Two')
        self.engine.serve('/summit/3/', title='Three')
        # Both requests are made before either answer is processed.
        self.engine.context.eval('navigateTo("/summit/2/"); navigateTo("/summit/3/");')
        while self.engine.context.execute_pending_job():
            pass
        self.assertEqual(self.engine.get('title'), 'Three')
        self.assertEqual(self.engine.get('pushed'), ['/summit/3/'])


class ControlsTests(SimpleTestCase):
    def setUp(self):
        self.engine = Engine()

    def test_kept_when_both_pages_have_them_so_the_checkbox_keeps_its_state(self):
        self.engine.serve('/col/5/', controls=True)
        self.engine.run('navigateTo("/col/5/");')
        self.assertIsNone(self.engine.get('controls.from'))     # still the original
        self.assertEqual(self.engine.get('controls.id'), 'map-controls')

    def test_dropped_on_a_river(self):
        self.engine.serve('/river/1/', controls=False)
        self.engine.run('navigateTo("/river/1/");')
        self.assertIn('controls removed', self.engine.get('log'))

    def test_brought_back_after_a_river(self):
        self.engine.run('controls = null;')
        self.engine.serve('/summit/4/', title='Four', controls=True)
        self.engine.run('navigateTo("/summit/4/");')
        self.assertEqual(self.engine.get('controls.from'), 'Four')


def click(anchor, **event):
    """A stand-in click on a link: `anchor` says where it points and where it sits."""
    return '''(function () {
        let prevented = false, followed = null;
        const saved = navigateTo;
        navigateTo = path => { followed = path; };
        const anchor = %s;
        const target = { closest: s => s === 'a[href]' ? {
            href: anchor.href, target: anchor.target || '',
            closest: s => anchor.inside === 'data' && s.includes('.detail-page .data')
                       || anchor.inside === 'popup' && s.includes('.map-popup') ? {} : null,
        } : null };
        try {
            followInPlace(Object.assign({ button: 0, defaultPrevented: false, target,
                                          preventDefault: () => { prevented = true; } }, %s));
        } finally {
            navigateTo = saved;
        }
        return { prevented, followed };
    })()''' % (json.dumps(anchor), json.dumps(event))


class LinkTests(SimpleTestCase):
    def setUp(self):
        self.engine = Engine()

    def followed(self, anchor, **event):
        return self.engine.get(click(anchor, **event))

    def test_a_link_in_the_data_pane_is_followed_in_place(self):
        self.assertEqual(self.followed({'href': 'https://treeline.test/col/5/', 'inside': 'data'}),
                         {'prevented': True, 'followed': '/col/5/'})

    def test_so_is_a_link_in_a_map_popup(self):
        self.assertEqual(
            self.followed({'href': 'https://treeline.test/summit/8/', 'inside': 'popup'}),
            {'prevented': True, 'followed': '/summit/8/'})

    def test_a_new_tab_is_still_a_new_tab(self):
        anchor = {'href': 'https://treeline.test/col/5/', 'inside': 'data'}
        for event in [{'ctrlKey': True}, {'metaKey': True}, {'shiftKey': True}, {'button': 1}]:
            with self.subTest(event=event):
                self.assertEqual(self.followed(anchor, **event),
                                 {'prevented': False, 'followed': None})

    def test_links_elsewhere_and_to_other_pages_are_left_alone(self):
        for anchor in [{'href': 'https://treeline.test/col/5/', 'inside': 'menu'},
                       {'href': 'https://treeline.test/point/5/', 'inside': 'data'},
                       {'href': 'https://treeline.test/col/5/', 'inside': 'data',
                        'target': '_blank'}]:
            with self.subTest(anchor=anchor):
                self.assertEqual(self.followed(anchor), {'prevented': False, 'followed': None})


class ServedPageTests(TestCase):
    """What the swap reads from a fetched page has to be there on every kind of it."""

    @classmethod
    def setUpTestData(cls):
        cls.parent = make_summit('Parent', 2600, 49.1, 20.1)
        cls.col = make_col('The Col', 1800, 48.98, 20.05)
        cls.summit = make_summit('Summit', 2400, 49.0, 20.0,
                                 key_col=cls.col, prominence_parent=cls.parent)

    def test_every_detail_page_has_a_data_pane_and_map_data(self):
        for path in (f'/summit/{self.summit.pk}/', f'/col/{self.col.pk}/'):
            with self.subTest(path=path):
                html = self.client.get(path).content.decode()
                self.assertIn('<div class="data">', html)
                self.assertIn('<script id="map-data" type="application/json">', html)

    def test_the_data_pane_carries_no_script_a_swap_would_silently_drop(self):
        """Markup set from another document does not run its scripts."""
        html = self.client.get(f'/summit/{self.summit.pk}/').content.decode()
        pane = html.split('<div class="data">')[1].split('<div id="map">')[0]
        self.assertNotIn('<script', pane)
