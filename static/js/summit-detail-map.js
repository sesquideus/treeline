// The map on a summit's page, and on the page comparing two of them: the peaks, their key
// cols, their parents, and the lines between. All of it is built server-side — by
// `build_summit_features()` for one summit, `build_comparison_features()` for a pair — and
// arrives in a single collection, so there is nothing to assemble here. The one decision
// left to the browser is whether those lines detour through the cols.
//
// Which is why the routing below works on *every* prominence line rather than the first:
// a comparison map draws one per link of the chain, and straightening only one of them
// would be worse than straightening none.

function makePopupHtml(feature) {
    const name = feature.get('name');
    const url = feature.get('url');
    if (!name) return null;
    if (url) return `${name} <a href="${url}" style="margin-left:8px;font-size:12px;">→ detail</a>`;
    return name;
}

// The prominence line is served routed — [summit, col, parent] — so straightening it is a
// matter of dropping the middle vertex. The routed shape is remembered on the feature
// rather than recomputed from anything else on the page: the col's position is only in
// that one geometry, and once the line is straightened it is gone.
function routeThroughCol(line, useRouting) {
    const routed = line.get('routed_shape');
    line.getGeometry().setCoordinates(
        useRouting ? routed : [routed[0], routed[routed.length - 1]]);
}

function initSummitMap(geojsonUrl, summitCoords) {
    // Optional, and that is the whole point of looking it up before the fetch: the summit
    // detail page has this checkbox, and the other pages that load this script do not.
    // Reaching through the null unconditionally is what used to throw here.
    const colToggle = document.getElementById('toggle-col');

    fetch(geojsonUrl).then(r => r.json()).then(geojson => {
        const { map, vectorLayer } = makeMap(geojson, styleFor, summitCoords);

        map.on('click', function(e) {
            const feature = map.forEachFeatureAtPixel(e.pixel, f => f);
            const html = feature ? makePopupHtml(feature) : null;
            if (html) {
                const popup = document.querySelector('.ol-popup');
                if (popup) popup.innerHTML = html;
            }
        });

        // Empty when the summit has no key col or no prominence parent, and when two peaks
        // compared sit in different trees: the server draws no line, so there is no route
        // to offer and the checkbox has nothing to do.
        const lines = vectorLayer.getSource().getFeatures()
            .filter(feature => feature.get('type') === 'prominence_line');

        if (lines.length && colToggle) {
            lines.forEach(line => line.set('routed_shape',
                                           line.getGeometry().getCoordinates()));
            const reroute = () => lines.forEach(line => routeThroughCol(line,
                                                                       colToggle.checked));
            reroute();
            colToggle.addEventListener('change', reroute);
        }
    });
}
