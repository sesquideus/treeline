
function segmentStyles(coords, c1, c2) {
    return coords.slice(0, -1).map((_, i) => new ol.style.Style({
        geometry: new ol.geom.LineString([coords[i], coords[i+1]]),
        stroke: new ol.style.Stroke({
            color: interpolateColor(c1, c2, i / (coords.length - 2)),
            width: 4,
        })
    }));
}


function makeMap(geojson, styleFor, coords) {
    const vectorLayer = new ol.layer.Vector({
        source: new ol.source.Vector({
            features: new ol.format.GeoJSON().readFeatures(geojson, {
                featureProjection: 'EPSG:3857',
            }),
        }),
        style: styleFor,
    });

    const map = new ol.Map({
        target: 'map',
        layers: [
            new ol.layer.Tile({
                 source: new ol.source.XYZ({
                    url: 'https://outdoor.tiles.freemap.sk/{z}/{x}/{y}',
                    tileLoadFunction: function(imageTile, src) {
                        imageTile.getImage().referrerPolicy = 'origin';
                        imageTile.getImage().src = src;
                    },
                    attributions: '© <a href="https://www.freemap.sk">Freemap.sk</a>, © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
                }),
            }),
            vectorLayer,
        ],
        view: new ol.View({ center: ol.proj.fromLonLat(coords), zoom: 9 }),
    });

    vectorLayer.getSource().once('change', function() {
        const extent = vectorLayer.getSource().getExtent();
        if (extent && isFinite(extent[0])) {
            map.getView().fit(extent, { padding: [60, 60, 60, 60] });
        }
    });

    const popup = document.createElement('div');
    popup.style.cssText = 'background:#fff;padding:6px 10px;border-radius:4px;font:14px sans-serif;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,0.3);';
    document.body.appendChild(popup);

    const overlay = new ol.Overlay({ element: popup, positioning: 'bottom-center', offset: [0, -10] });
    map.addOverlay(overlay);

    map.on('click', function(e) {
        const feature = map.forEachFeatureAtPixel(e.pixel, f => f);
        if (feature && feature.get('name')) {
            overlay.setPosition(e.coordinate);
            popup.textContent = feature.get('name');
        } else {
            overlay.setPosition(undefined);
        }
    });

    return map;
}


function fadedSegmentStyles(coords, c1, c2, alpha, width) {
    return coords.slice(0, -1).map((_, i) => new ol.style.Style({
        geometry: new ol.geom.LineString([coords[i], coords[i+1]]),
        stroke: new ol.style.Stroke({
            color: interpolateColor(c1, c2, i / (coords.length - 2)).replace('rgb', 'rgba').replace(')', `,${alpha})`),
            width,
        })
    }));
}

function lonLatToMercator(lon, lat) {
    return ol.proj.fromLonLat([lon, lat]);
}

function buildLineageLayer(summits, useIsolation, useRouting) {
    const format = new ol.format.GeoJSON();
    const vectorSource = new ol.source.Vector();

    // index features by pk for parent lookup
    const byPk = {};
    summits.features.forEach(f => {
        byPk[f.properties.pk] = f;
    });

    summits.features.forEach(feature => {
        const props = feature.properties;
        const parentPk = useIsolation ? props.isolation_parent : props.prominence_parent;
        if (!parentPk || !byPk[parentPk]) return;

        const parent = byPk[parentPk];
        const fromCoord = feature.geometry.coordinates;
        const toCoord = parent.geometry.coordinates;

        let coords;

        if (useRouting) {
            if (useIsolation && props.ilp && props.ilp.lon != null && props.ilp.lat != null) {
                // peak → nearest highest point → parent peak
                coords = [
                    fromCoord,
                    ol.proj.fromLonLat([props.ilp.lon, props.ilp.lat]),
                    ol.proj.fromLonLat(toCoord),
                ];
            } else if (!useIsolation && props.kc && props.kc.lon != null && props.kc.lat != null) {
                // peak → key col → parent peak
                coords = [
                    ol.proj.fromLonLat(fromCoord),
                    ol.proj.fromLonLat([props.kc.lon, props.kc.lat]),
                    ol.proj.fromLonLat(toCoord),
                ];
            } else {
                // routing requested but data missing — fall back to straight line
                coords = [
                    ol.proj.fromLonLat(fromCoord),
                    ol.proj.fromLonLat(toCoord),
                ];
            }
        } else {
            coords = [
                ol.proj.fromLonLat(fromCoord),
                ol.proj.fromLonLat(toCoord),
            ];
        }

        vectorSource.addFeature(new ol.Feature({
            geometry: new ol.geom.LineString(coords),
            pk: props.pk,
            parent_pk: parentPk,
        }));
    });

    return new ol.layer.Vector({
        source: vectorSource,
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({
                color: useIsolation ? '#e07020' : '#FF40FF',
                width: 3,
            }),
        }),
    });
}

function buildKeyColLayer(summits) {
    const vectorSource = new ol.source.Vector();

    summits.features.forEach(feature => {
        const kc = feature.properties.kc;
        if (!kc || kc.lon == null || kc.lat == null) return;

        vectorSource.addFeature(new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat([kc.lon, kc.lat])),
            name: kc.name,
            alt: kc.alt,
            pk: kc.pk,
            type: 'col',
        }));
    });

    return new ol.layer.Vector({
        source: vectorSource,
        style: new ol.style.Style({
            image: new ol.style.RegularShape({
                points: 4,
                radius: 6,
                angle: Math.PI / 4,  // diamond shape
                fill: new ol.style.Fill({ color: '#c04040' }),
                stroke: new ol.style.Stroke({ color: '#ffffff', width: 1 }),
            }),
        }),
    });
}

function buildRiversLayer(rivers) {
    const vectorSource = new ol.source.Vector();
    const format = new ol.format.GeoJSON();

    const features = format.readFeatures(rivers, {
        featureProjection: 'EPSG:3857',
    });
    vectorSource.addFeatures(features);

    return new ol.layer.Vector({
        source: vectorSource,
        style: new ol.style.Style({
            stroke: new ol.style.Stroke({
                color: '#1a40f9',
                width: 3,
            }),
        }),
    });
}

function initGlobalMap(summitsUrl, riversUrl) {
    let map, lineageLayer;
    let summitsData;

    const treeToggle = document.getElementById('toggle-tree');
    const routeToggle = document.getElementById('toggle-routing');

    function rebuildLineage() {
        if (!map || !summitsData) return;
        const useIsolation = treeToggle && treeToggle.checked;
        const useRouting = routeToggle && routeToggle.checked;
        if (lineageLayer) map.removeLayer(lineageLayer);
        lineageLayer = buildLineageLayer(summitsData, useIsolation, useRouting);
        map.addLayer(lineageLayer);
    }

    Promise.all([
        fetch(summitsUrl).then(r => r.json()),
        fetch(riversUrl).then(r => r.json()),
    ]).then(([summits, rivers]) => {
        summitsData = summits;
        map = makeMap(summits, styleFor, [19.5, 48.5]);
        map.addLayer(buildRiversLayer(rivers));
        map.addLayer(buildKeyColLayer(summits));  // add col markers
        rebuildLineage();

        map.on('click', function(e) {
            const feature = map.forEachFeatureAtPixel(e.pixel, f => f);
            const html = feature ? makePopupHtml(feature) : null;
            if (html) {
                const popup = document.querySelector('.ol-popup');
                if (popup) popup.innerHTML = html;
            }
        });

        map.addLayer(buildLineageLayer(rivers, false, false));
        rebuildLineage();

        if (treeToggle) treeToggle.addEventListener('change', rebuildLineage);
        if (routeToggle) routeToggle.addEventListener('change', rebuildLineage);
    });
}
