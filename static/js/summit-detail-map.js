function makePopupHtml(feature) {
    const name = feature.get('name');
    const url = feature.get('url');
    if (!name) return null;
    if (url) return `${name} <a href="${url}" style="margin-left:8px;font-size:12px;">→ detail</a>`;
    return name;
}

function initSummitMap(geojsonUrl, lineageUrl, summitCoords) {
    let map, lineageLayer;

    Promise.all([
        fetch(geojsonUrl).then(r => r.json()),
        fetch(lineageUrl).then(r => r.json()),
    ]).then(([geojson, lineage]) => {
        map = makeMap(geojson, styleFor, summitCoords);

        map.on('click', function(e) {
            const feature = map.forEachFeatureAtPixel(e.pixel, f => f);
            const html = feature ? makePopupHtml(feature) : null;
            if (html) {
                const popup = document.querySelector('.ol-popup');
                if (popup) popup.innerHTML = html;
            }
        });

        lineageLayer = buildLineageLayer(lineage, document.getElementById('toggle-col').checked);
        map.addLayer(lineageLayer);
    });

    document.getElementById('toggle-col').addEventListener('change', function() {
        if (!map) return;
        map.removeLayer(lineageLayer);
        fetch(lineageUrl).then(r => r.json()).then(lineage => {
            lineageLayer = buildLineageLayer(lineage, this.checked);
            map.addLayer(lineageLayer);
        });
    });
}