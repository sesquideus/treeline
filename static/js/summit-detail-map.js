// The map on the summit, col and river pages, and on the page comparing two summits: the
// object, what it is related to, and the lines between. All of it is built server-side — by
// `build_summit_features()` for one summit, `build_comparison_features()` for a pair, and
// `col_features()` / `river_features()` in mountains/views/detail_map.py — and arrives in a
// single collection, so there is nothing to assemble here. The one decision left to the
// browser is whether the prominence lines detour through the cols.
//
// Which is why the routing below works on *every* prominence line rather than the first:
// a comparison map draws one per link of the chain, and straightening only one of them
// would be worse than straightening none.
//
// Moving from one summit, col or river to another does not reload the page. A link in the
// data pane, in a map popup, or a click on a mark fetches the next page, and only its data
// pane, its title and its map features are swapped in; OpenLayers, the tiles already
// loaded and the map itself stay. The address bar is kept honest with the History API —
// the URL is the real `/summit/<pk>/`, so it can be shared, reloaded or opened in a new
// tab and the server renders the same page — and Back and Forward swap the same way.
// Anything that goes wrong falls back to an ordinary navigation.

// The prominence line is served routed — [summit, col, parent] — so straightening it is a
// matter of dropping the middle vertex. The routed shape is remembered on the feature
// rather than recomputed from anything else on the page: the col's position is only in
// that one geometry, and once the line is straightened it is gone.
function routeThroughCol(line, useRouting) {
    const routed = line.get('routed_shape');
    line.getGeometry().setCoordinates(
        useRouting ? routed : [routed[0], routed[routed.length - 1]]);
}

// The pages whose content can be swapped for one another: they share one layout
// (`core/detail-page.html`) and differ only in the data pane and the map.
const DETAIL_PATH = /^\/(summit|col|river)\/\d+\/$/;

// The path of `href` if it is one of those pages on this site, otherwise null. A query or a
// fragment means something other than the page itself, so it is left to the browser.
function detailPath(href, origin) {
    const path = href.startsWith(origin + '/') ? href.slice(origin.length) : href;
    return DETAIL_PATH.test(path) ? path : null;
}

// Whether a click should be left to the browser: another button, a modifier asking for a
// new tab or window, a link with a target of its own, or one some other handler took.
function isPlainClick(event) {
    return event.button === 0 && !event.defaultPrevented
        && !(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey);
}

// The live map, once it exists: what a swap needs to put new features on it.
let detailMap = null;

function readFeatures(geojson) {
    return new ol.format.GeoJSON().readFeatures(geojson, { featureProjection: 'EPSG:3857' });
}

// The lines served routed through a col: a summit's climb to its parent, and on a summit's
// page each child's climb up to it.
const ROUTED_LINES = ['prominence_line', 'child_line'];

// Remember each prominence line's routed shape and route it as the checkbox says. Run again
// after every swap: the lines are new, and the checkbox may be too. Assigned with
// `onchange` rather than `addEventListener`, so a checkbox kept across a swap does not
// collect one listener per page visited, each holding the previous page's lines.
//
// No lines when the summit has no key col or no prominence parent, and when two peaks
// compared sit in different trees — and no checkbox on a river's page.
function wireRouting(source) {
    const colToggle = document.getElementById('toggle-col');
    const lines = source.getFeatures()
        .filter(feature => ROUTED_LINES.includes(feature.get('type')));
    lines.forEach(line => line.set('routed_shape', line.getGeometry().getCoordinates()));
    if (!colToggle) return;
    const reroute = () => lines.forEach(line => routeThroughCol(line, colToggle.checked));
    colToggle.onchange = reroute;
    reroute();
}

// `source` is either the URL of the collection — the comparison map fetches its own — or
// the collection itself, which the detail pages embed in the page.
function initDetailMap(source) {
    const loaded = typeof source === 'string'
        ? fetch(source).then(r => r.json())
        : Promise.resolve(source);
    const swappable = detailPath(window.location.href, window.location.origin) !== null;

    loaded.then(geojson => {
        // No centre and no zoom: makeMap frames the features marked `frame`. A click on a
        // mark navigates in place on the detail pages; the comparison page keeps opening
        // a new tab, so the pair under comparison is not lost.
        const { map, vectorLayer, clearPopup } = makeMap(
            geojson, styleFor, undefined, undefined, undefined,
            swappable ? url => navigateTo(url) : undefined);
        detailMap = { map, source: vectorLayer.getSource(), clearPopup };
        wireRouting(detailMap.source);
    });

    if (swappable) {
        // The entry the visit started on gets a state too, so Back to it is a swap as well.
        history.replaceState({ detail: true }, '');
        document.addEventListener('click', followInPlace);
        window.addEventListener('popstate', () => {
            const path = detailPath(window.location.href, window.location.origin);
            if (path) navigateTo(path, false);
        });
    }
}

// A link to another detail page, in the data pane or a map popup, is followed in place.
function followInPlace(event) {
    if (!detailMap || !isPlainClick(event)) return;
    const anchor = event.target.closest('a[href]');
    if (!anchor || anchor.target || !anchor.closest('.detail-page .data, .map-popup')) return;
    const path = detailPath(anchor.href, window.location.origin);
    if (!path) return;
    event.preventDefault();
    navigateTo(path);
}

// The page's own map controls. Kept when both pages have them, so the checkbox keeps the
// state the reader gave it; added or dropped when only one does (a river has none).
function swapControls(incoming) {
    const current = document.getElementById('map-controls');
    if (current && !incoming) current.remove();
    if (!current && incoming) document.getElementById('map').after(incoming);
}

let navigation = 0;     // so a slow response to an earlier click cannot overwrite a later one

function navigateTo(path, push = true) {
    const ticket = ++navigation;
    return fetch(path, { headers: { Accept: 'text/html' } })
        .then(response => {
            if (!response.ok) throw new Error(`${response.status} for ${path}`);
            return response.text();
        })
        .then(html => {
            if (ticket !== navigation) return;
            const page = new DOMParser().parseFromString(html, 'text/html');
            const data = page.querySelector('.detail-page .data');
            const features = page.getElementById('map-data');
            if (!data || !features) throw new Error(`${path} is not a detail page`);

            document.querySelector('.detail-page .data').replaceWith(data);
            swapControls(page.getElementById('map-controls'));
            document.title = page.title;
            if (push) history.pushState({ detail: true }, '', path);
            window.scrollTo(0, 0);

            detailMap.clearPopup();
            detailMap.source.clear();
            detailMap.source.addFeatures(readFeatures(JSON.parse(features.textContent)));
            fitToFramed(detailMap.map, detailMap.source);
            wireRouting(detailMap.source);
        })
        .catch(() => { window.location.href = path; });
}
