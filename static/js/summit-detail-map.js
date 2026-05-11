const circleStyle = new ol.style.Style({
    stroke: new ol.style.Stroke({ color: 'rgba(219,40,12,0.6)', width: 1.5, lineDash: [6,4] }),
    fill:   new ol.style.Fill({ color: 'rgba(219,40,12,0.16)' }),
});

function styleFor(feature) {
    switch (feature.get('type')) {
        case 'summit':                return summit('#c0392b', 8);
        case 'isolation_point':       return dot('#f1c40f');
        case 'isolation_parent':      return dot('#8e44ad');
        case 'col':                   return colMarker();
        case 'prominence_parent':     return summit('#2980b9');
        case 'encirclement_parent':   return dot('#e67e22');
        case 'isolation_circle':      return circleStyle;
        case 'isolation_line_first':  return gradientLine(feature, GREEN, YELLOW);
        case 'isolation_line_second': return gradientLine(feature, YELLOW, PURPLE);
        case 'prominence_line_first': return gradientLine(feature, BLUE, RED);
        case 'prominence_line_second':return gradientLine(feature, RED, YELLOW);
        case 'encirclement_line':     return [dashedLine('rgba(35,14,4,0.8)')];
        default: return [];
    }
}

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