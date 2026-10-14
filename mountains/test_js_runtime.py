"""
Running the map's JavaScript — `static/js/map.js` and `static/js/styles.js` — from the tests.

Most of what the map shows is built in JavaScript, and for a long time the only thing the
suite could say about it was that some string appeared in the file. That catches a deleted
line and nothing else: it cannot tell whether the popup renders a link or plain text,
whether an empty section is dropped or left as a stray caption, or whether a row lands in
the section it was meant for.

`quickjs` is an embeddable engine, so the popup builders can simply be run. Neither file
needs a live map to load — `map.js` only declares, and the few objects `styles.js` builds as
it loads are satisfied by a stub — so a file evaluates against `STUBS` and `popupHtml()`,
the real dispatcher rather than a copy of it, can then be called with a feature built here.
`styles.js` loads the same way, which is how the prominence bands are read back and checked
against the Python copy.

What this cannot do is anything that needs OpenLayers or a DOM: layer construction, hit
testing, the hover highlight. Those stay source-asserted, or untested.
"""

import json
import re
from functools import lru_cache
from html import unescape

MAP_JS = 'static/js/map.js'
STYLES_JS = 'static/js/styles.js'
SEARCH_JS = 'static/js/summit-search.js'

#: The names the scripts reach for at the top level. `map.js` only declares, so empty
#: objects carry it; `styles.js` builds a handful of fixed `ol.style` objects as it loads,
#: which is why `ol` has to be something that can be read through and constructed from. The
#: proxy answers any property with itself and any `new` with a bare object — enough to get
#: the file loaded, and useless for anything that would actually need OpenLayers, which is
#: the honest state of affairs here.
STUBS = """
var ol = new Proxy(function () {}, {
    get: (target, key) => ol,
    construct: () => ({}),
    apply: () => ({}),
});
var document = { getElementById: () => null, querySelector: () => null,
                 querySelectorAll: () => [], createElement: () => ({ style: {} }),
                 addEventListener: () => {} };
var window = {};
var performance = { now: () => 0 };
var requestAnimationFrame = () => 0;
var cancelAnimationFrame = () => 0;
var setTimeout = () => 0;
var clearTimeout = () => 0;
var fetch = () => null;
"""


@lru_cache(maxsize=None)
def _context(source=MAP_JS):
    """
    One engine per file, kept for the process.

    A context per file rather than one with everything loaded: `styles.js` and `map.js` are
    separate scripts sharing a global scope in the browser, and loading both here would let
    a test pass on a name the file under test does not actually declare.
    """
    import quickjs

    context = quickjs.Context()
    context.eval(STUBS)
    with open(source, encoding='utf-8') as handle:
        context.eval(handle.read())
    return context


def evaluate(expression, source=MAP_JS):
    """
    Evaluate a JavaScript expression against one of the scripts and bring the result back.

    The value crosses as JSON, so what Python receives is a plain dict/list/str/number
    rather than a handle to a live JS object — which is what makes assertions on it read
    normally. `undefined` comes back as None, the same as `null`.
    """
    return json.loads(_context(source).eval('JSON.stringify(%s) ?? "null"' % expression))


def call(function, *arguments, source=MAP_JS):
    """`evaluate`, for the common case of calling one function with JSON-able arguments."""
    return evaluate('%s(%s)' % (function, ', '.join(json.dumps(a) for a in arguments)),
                    source=source)


def feature_expression(properties):
    """
    A stand-in for an OpenLayers feature, as a JS expression.

    `map.js` reads features only through `.get()`, so an object with that one method is
    enough. The parentheses around the object literal are not optional: without them the
    arrow function's body parses as a block rather than an object.
    """
    return '{ get: key => (%s)[key] }' % json.dumps(properties)


def popup_html(**properties):
    """
    The popup `map.js` builds for a feature with these properties.

    `popupHtml()` reads a feature only through `.get()`, so a plain object with that one
    method stands in for the OpenLayers one. Values cross as JSON, which is exactly how they
    arrive in the browser — they came from `to_dict()` through a GeoJSON endpoint.
    """
    context = _context()
    context.eval('var __feature_properties = %s;' % json.dumps(properties))
    return context.eval('popupHtml({ get: key => __feature_properties[key] })')


def rows(html):
    """
    The popup's table as `(section, label, value)` triples, section carried down the rows it
    spans the way the rendered table does.

    Parsing rather than matching substrings: a test that says a value belongs to the
    `watershed HP` row should fail when it moves to another section, and a substring search
    cannot see that.
    """
    parsed, section = [], ''
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.S):
        cells = re.findall(r'<t([hd])[^>]*>(.*?)</t[hd]>', row, re.S)
        headers = [content for kind, content in cells if kind == 'h']
        values = [content for kind, content in cells if kind == 'd']
        if '<th class="section"' in row:
            section = headers.pop(0)
        label = headers[0] if headers else ''
        parsed.append((section, label, values[0] if values else ''))
    return parsed


def text(markup):
    """
    Markup stripped and entities resolved — for assertions about what a reader sees.

    `&nbsp;` has to be unescaped rather than left alone: the builders emit it as an entity,
    and a test comparing against a plain space would otherwise fail for a reason that has
    nothing to do with the popup.
    """
    stripped = unescape(re.sub(r'<[^>]+>', '', markup))
    return re.sub(r'\s+', ' ', stripped.replace('\xa0', ' ')).strip()
