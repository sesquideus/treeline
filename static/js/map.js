// Layer draw order. Layers are added and removed as modes change, so ordering is pinned
// with an explicit zIndex rather than left to insertion order. Summits go on top: they are
// the click targets, and a marker hidden under a lineage line cannot be hit.
const Z_RIVERS         = 10;
const Z_CONFLUENCE     = 15;
const Z_LINEAGE        = 20;
const Z_HIGHLIGHT_LINE = 25;   // over the lineage it traces, under the markers it connects
const Z_OVERLAY_POINTS = 30;
const Z_SUMMITS        = 40;
const Z_HIGHLIGHT_MARK = 45;   // above the summits: the grown parent replaces its own marker

// `onHover` is optional and receives the feature under the cursor (or null). The tooltip is
// wired up here; anything else that should react to hovering — the key col and parent
// highlight, for one — goes through the callback rather than adding a second pointermove
// listener, so the hit test still runs once per mouse position.
// Country flags for a popup caption, same images the tables use. Wrapped in one nowrap span
// so the caption never breaks between a name and its flags, or in the middle of a pair.
function flagsHtml(codes) {
    if (!codes || !codes.length) return '';
    const images = codes.map(code =>
        `<img class="flag" src="https://flagpedia.net/data/flags/mini/${code}.png" alt="${code}">`
    ).join('');
    return `<span class="flags">${images}</span>`;
}

function makeMap(geojson, styleFor, coords, zoom, onHover) {
    const tileLayer = new ol.layer.Tile({
        opacity: 0.4,
        source: new ol.source.XYZ({
            url: 'https://outdoor.tiles.freemap.sk/{z}/{x}/{y}',
            tileLoadFunction: function(imageTile, src) {
                imageTile.getImage().referrerPolicy = 'origin';
                imageTile.getImage().src = src;
            },
            attributions: '© <a href="https://www.freemap.sk">Freemap.sk</a>, © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }),
    })

    const vectorLayer = new ol.layer.Vector({
        source: new ol.source.Vector({
            features: new ol.format.GeoJSON().readFeatures(geojson, {
                featureProjection: 'EPSG:3857',
            }),
        }),
        style: styleFor,
        zIndex: Z_SUMMITS,
    });

    const map = new ol.Map({
        target: 'map',
        layers: [
            tileLayer,
            vectorLayer,
        ],
        view: new ol.View({ center: ol.proj.fromLonLat(coords), zoom: zoom }),
    });

    vectorLayer.getSource().once('change', function() {
        const extent = vectorLayer.getSource().getExtent();
        if (extent && isFinite(extent[0])) {
            map.getView().fit(extent, { padding: [60, 60, 60, 60] });
        }
    });

    const popup = document.createElement('div');
    popup.className = 'map-popup';
    popup.style.cssText ='background:#fff;padding:6px 10px;border-radius:4px;font:14px sans-serif;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,0.3);';
    document.body.appendChild(popup);

    const overlay = new ol.Overlay({ element: popup, positioning: 'bottom-center', offset: [0, -10] });
    map.addOverlay(overlay);

    function popupHtml(feature) {
        if (feature) {
            let text = "";
            switch (feature.get('type')) {
                case 'summit': {
                    const keyCol = feature.get('key_col');
                    const keyColRows = keyCol
                        ? `
                            <tr>
                                <th>key col</th>
                                <td>${keyCol.name ?? 'unnamed'}</td>
                            </tr>
                            <tr>
                                <th>↳ altitude</th>
                                <td class="altitude">${keyCol.alt?.toFixed(1) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>↳ distance</th>
                                <td class="distance">${keyCol.dist != null
                                    ? (keyCol.dist / 1000).toFixed(3)
                                    : '?'}</td>
                            </tr>
                            <tr>
                                <th>↳ drop</th>
                                <td class="altitude">${keyCol.drop?.toFixed(1) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>↳ slope</th>
                                <td class="slope">${keyCol.slope != null
                                    ? (keyCol.slope * 1000).toFixed(2)
                                    : '?'}</td>
                            </tr>
                          `
                        : `
                            <tr>
                                <th>key col</th>
                                <td>—</td>
                            </tr>
                          `;
                    text = `
                        <h3 class="mountain">
                            ${flagsHtml(feature.get('countries'))}
                            ${feature.get('name') ?? 'unnamed peak'}
                        </h3>
                        <table class="tooltip">
                            <tr>
                                <th>altitude</th>
                                <td class="altitude">${feature.get('alt')?.toFixed(1) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>prominence</th>
                                <td class="altitude">${feature.get('prom')?.toFixed(1) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>class</th>
                                <td>${prominenceBand(feature.get('prom')).label}</td>
                            </tr>
                            ${keyColRows}
                        </table>
                    `;
                    break;
                }
                case 'col': {
                    const confluence = feature.get('confluence');
                    const confluenceRows = confluence
                        ? `
                            <tr>
                                <th>confluence</th>
                                <td class="link river">
                                    <a href="">
                                        ${confluence.name ?? 'unnamed'}
                                    </a>
                                </td>
                            </tr>
                            <tr>
                                <th>↳ latitude</th>
                                <td class="angle">${confluence.lat?.toFixed(5) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>↳ longitude</th>
                                <td class="angle">${confluence.lon?.toFixed(5) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>↳ altitude</th>
                                <td class="altitude">${confluence.alt?.toFixed(1) ?? '?'}</td>
                            </tr>
                            <tr>
                                <th>↳ distance</th>
                                <td class="distance">${confluence.dist != null
                                    ? (confluence.dist / 1000).toFixed(3)
                                    : '?'}</td>
                            </tr>
                          `
                        : `
                            <tr>
                                <td>confluence</td>
                                <td>—</td>
                            </tr>
                          `;
                    text = `
                        <h3 class="col">
                            ${flagsHtml(feature.get('countries'))}
                            ${feature.get('name') ?? 'unknown'}
                        </h3>
                        <table>
                            <tr>
                                <td>name</td>
                                <td>${feature.get('name') ?? 'unnamed col'}</td>
                            </tr>
                            <tr>
                                <td>key col for</td>
                                <td>${feature.get('key_for') ?? '???'}</td>
                            </tr>
                            <tr>
                                <td>altitude</td>
                                <td class="altitude">${feature.get('alt')?.toFixed(1) ?? '?'}</td>
                            </tr>
                            <tr>
                                <td>depth</td>
                                <td class="altitude">${feature.get('depth')?.toFixed(1) ?? '?'}</td>
                            </tr>
                            ${confluenceRows}
                        </table>
                    `;
                    break;
                }
                case 'river':
                    text = `
                        <h3 class="river">
                            ${feature.get('name') ?? 'unknown'}
                        </h3>
                        <table>
                            <tr>
                                <td></td>
                            </tr>
                            <tr>
                                <td>flows into</td>
                                <td>
                                    ${feature.get('parent')
                                        ? `<a href="river/${feature.get('parent')['id']}">${feature.get('parent')['name']}</a>`
                                        : '—'}
                                </td>
                            </tr>
                        </table>
                    `;
            }
            return text;
        }
        return '';
    }

    // Which feature the popup is currently describing. `pointermove` fires on every mouse
    // position, and rebuilding the body each time would re-run the templates and the band
    // lookup dozens of times a second, so the HTML is only replaced when the feature changes.
    let described = null;

    function showPopup(feature, coordinate) {
        const html = feature ? popupHtml(feature) : '';
        if (!html) {                       // no feature, or one the switch does not describe
            described = null;
            overlay.setPosition(undefined);
            return;
        }
        if (feature !== described) {
            popup.innerHTML = html;
            described = feature;
        }
        // Anchored to the mark itself rather than to the cursor, so it does not jitter while
        // the pointer moves around inside a marker. Lines have no single point to sit on.
        const geometry = feature.getGeometry();
        overlay.setPosition(geometry && geometry.getType() === 'Point'
            ? geometry.getCoordinates()
            : coordinate);
    }

    // The highlight rings must not be hoverable themselves: they sit on top of the very markers
    // they annotate, so hit-testing them would hand back a ring instead of the peak and the
    // tooltip would blank out the moment a highlight appeared.
    function featureAt(pixel) {
        return map.forEachFeatureAtPixel(pixel, f => f,
            { layerFilter: layer => !(layer.get('name') || '').startsWith('highlight') });
    }

    // A col under the cursor always shows its way down to the confluence, whatever the
    // col–confluence toggle is set to. It needs nothing but the feature's own properties, so
    // it lives here rather than in the onHover callback and works on the detail map too.
    // The "highlight" prefix keeps the line out of hit testing — it starts on the very marker
    // it belongs to, and hovering it instead of the col would clear it again.
    const confluenceHoverSource = new ol.source.Vector();
    const confluenceHoverLayer = new ol.layer.Vector({
        source: confluenceHoverSource,
        style: confluenceLineStyle,
        zIndex: Z_HIGHLIGHT_LINE,
    });
    confluenceHoverLayer.set('name', 'highlight-confluence');
    map.addLayer(confluenceHoverLayer);

    let confluenceShown = null;

    function showConfluence(feature) {
        const col = feature && feature.get('type') === 'col' ? feature : null;
        if (col === confluenceShown) return;    // same col: leave the line alone
        confluenceShown = col;
        confluenceHoverSource.clear();

        const confluence = col && col.get('confluence');
        if (!confluence || confluence.lon == null) return;
        confluenceHoverSource.addFeature(confluenceLine(
            col.getGeometry().getCoordinates(),    // already in the view projection
            ol.proj.fromLonLat([confluence.lon, confluence.lat]),
        ));
    }

    function hovered(feature, coordinate) {
        showPopup(feature, coordinate);
        showConfluence(feature);
        if (onHover) onHover(feature);
    }

    map.on('pointermove', function(e) {
        if (e.dragging) {                  // panning: the popup would trail the drag
            hovered(null);
            return;
        }
        const feature = featureAt(e.pixel);
        map.getTargetElement().style.cursor = feature ? 'pointer' : '';
        hovered(feature, e.coordinate);
    });

    // pointermove stops firing once the cursor leaves the canvas, which would otherwise leave
    // the last popup stuck on screen.
    map.getViewport().addEventListener('pointerleave', () => hovered(null));

    // Touch devices have no hover at all, so a tap still opens the popup.
    map.on('click', function(e) {
        hovered(featureAt(e.pixel), e.coordinate);
    });

    const opacitySlider = document.getElementById('map-opacity');
    if (opacitySlider) {
        opacitySlider.addEventListener('input', function() {
            tileLayer.setOpacity(this.value / 100);
        });
    }

    return { map, tileLayer, vectorLayer };
}

const PROMINENCE_PEAK_TO_COL_A   = [180, 0,   255, 1];   // purple
const PROMINENCE_PEAK_TO_COL_B   = [0,   180, 255, 1];   // cyan
const PROMINENCE_COL_TO_PARENT_A = [0,   180, 255, 1];   // cyan
const PROMINENCE_COL_TO_PARENT_B = [0,   255, 180, 1];   // turquoise

const ISOLATION_PEAK_TO_NHP_A    = [255,   0,   0,  1];
const ISOLATION_PEAK_TO_NHP_B    = [255,   0, 255,  1];
const ISOLATION_NHP_TO_PARENT_A  = [255,   0, 255,  1];
const ISOLATION_NHP_TO_PARENT_B  = [  0,   0, 255,  1];

const SLOPE_COLOUR_A             = [50,  50,  220, 1];
const SLOPE_COLOUR_B             = [180, 220, 255, 1];

const HORIZON_COLOUR_A           = [180, 50,  180, 1];
const HORIZON_COLOUR_B           = [255, 200, 255, 1];

const CONFLUENCE_COLOUR_A        = [255, 100, 255, 1];   // pink at the col
const CONFLUENCE_COLOUR_B        = [255, 200, 255, 1];   // pale pink at the confluence

// One definition of the pink col → confluence line, shared by the layer the toggle switches
// on and the one a hovered col draws for itself, so the two cannot drift apart.
function confluenceLineStyle(feature) {
    return segmentStyles(
        denseCoords(feature.getGeometry().getCoordinates()),
        CONFLUENCE_COLOUR_A,
        CONFLUENCE_COLOUR_B,
    );
}

// Both endpoints in the view projection.
function confluenceLine(colCoord, confluenceCoord) {
    return new ol.Feature({
        geometry: new ol.geom.LineString([colCoord, confluenceCoord]),
        type: 'confluence_line',
    });
}

function lineageStyle(mode, useRouting) {
    return feature => {
        const segment = feature.get('segment');

        switch (mode) {
            case 'prominence':
                switch (segment) {
                    case 'peak_to_col':
                        return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                            PROMINENCE_PEAK_TO_COL_A, PROMINENCE_PEAK_TO_COL_B);
                    case 'col_to_parent':
                        return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                            PROMINENCE_COL_TO_PARENT_A, PROMINENCE_COL_TO_PARENT_B);
                    default:
                        return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                            PROMINENCE_PEAK_TO_COL_A, PROMINENCE_COL_TO_PARENT_B);
                }

            case 'isolation':
                switch (segment) {
                    case 'peak_to_nhp':
                        return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                            ISOLATION_PEAK_TO_NHP_A, ISOLATION_PEAK_TO_NHP_B);
                    case 'nhp_to_parent':
                        return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                            ISOLATION_NHP_TO_PARENT_A, ISOLATION_NHP_TO_PARENT_B);
                    default:
                        return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                            ISOLATION_PEAK_TO_NHP_A, ISOLATION_NHP_TO_PARENT_B);
                }

            case 'slope':
                return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                    SLOPE_COLOUR_A, SLOPE_COLOUR_B);

            case 'horizon':
                return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()),
                    HORIZON_COLOUR_A, HORIZON_COLOUR_B);

            default:
                return [];
        }
    };
}


function lineageWaypoint(mode, props, useRouting, byColPk) {
    if (!useRouting) return null;
    switch (mode) {
        case 'prominence':
            const col = byColPk[props.kc];
            return col ? [col.geometry.coordinates[0], col.geometry.coordinates[1]] : null;
        case 'isolation':
            return props.ilp && props.ilp.lon != null ? [props.ilp.lon, props.ilp.lat] : null;
        default:
            return null;
    }
}


function lineageParentAttr(mode) {
    switch (mode) {
        case 'prominence': return 'prominence_parent';
        case 'isolation':  return 'isolation_parent';
        case 'slope':      return 'slope_parent';
        case 'horizon':    return 'horizon_parent';
        default:           return null;
    }
}

function buildLineageLayer(summits, cols, mode, useRouting) {
    const vectorSource = new ol.source.Vector();
    const parentAttr = lineageParentAttr(mode);
    if (!parentAttr) return new ol.layer.Vector({ source: vectorSource });

    const byPk = {};
    summits.features.forEach(f => { byPk[f.properties.pk] = f; });

    const byColPk = {};
    cols.features.forEach(f => { byColPk[f.properties.pk] = f; });

    summits.features.forEach(feature => {
        const props = feature.properties;
        const parentPk = props[parentAttr];
        const fromCoord = feature.geometry.coordinates;

        // special case: isolation mode with NHP but no parent
        if (mode === 'isolation' && useRouting && props.ilp && props.ilp.lon != null && !parentPk) {
            vectorSource.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString([
                    ol.proj.fromLonLat(fromCoord),
                    ol.proj.fromLonLat([props.ilp.lon, props.ilp.lat]),
                ]),
                pk: props.pk,
                segment: 'peak_to_nhp',
            }));
            return;
        }

        if (!parentPk || !byPk[parentPk]) return;


        const parent = byPk[parentPk];
        const toCoord = parent.geometry.coordinates;
        const waypoint = lineageWaypoint(mode, props, useRouting, byColPk);

        if (mode === 'prominence' && useRouting && waypoint) {
            // peak → key col
            vectorSource.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString([
                    ol.proj.fromLonLat(fromCoord),
                    ol.proj.fromLonLat(waypoint),
                ]),
                pk: props.pk,
                segment: 'peak_to_col',
            }));
            // key col → parent
            vectorSource.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString([
                    ol.proj.fromLonLat(waypoint),
                    ol.proj.fromLonLat(toCoord),
                ]),
                pk: props.pk,
                segment: 'col_to_parent',
            }));
            return;
        }
        if (mode === 'isolation' && useRouting && waypoint) {
            // peak → NHP
            vectorSource.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString([
                    ol.proj.fromLonLat(fromCoord),
                    ol.proj.fromLonLat(waypoint),
                ]),
                pk: props.pk,
                segment: 'peak_to_nhp',
            }));
            // NHP → parent
            vectorSource.addFeature(new ol.Feature({
                geometry: new ol.geom.LineString([
                    ol.proj.fromLonLat(waypoint),
                    ol.proj.fromLonLat(toCoord),
                ]),
                pk: props.pk,
                segment: 'nhp_to_parent',
            }));
            return;
        }

// all other modes — single line
        const coords = waypoint
            ? [ol.proj.fromLonLat(fromCoord), ol.proj.fromLonLat(waypoint), ol.proj.fromLonLat(toCoord)]
            : [ol.proj.fromLonLat(fromCoord), ol.proj.fromLonLat(toCoord)];
        vectorSource.addFeature(new ol.Feature({
            geometry: new ol.geom.LineString(coords),
            pk: props.pk,
            parent_pk: parentPk,
        }));
    });

    return new ol.layer.Vector({
        source: vectorSource,
        style: lineageStyle(mode, useRouting),
    });
}

function buildKeyColLayer(summits, cols) {
    const vectorSource = new ol.source.Vector();
    const byColPk = {};
    cols.features.forEach(f => { byColPk[f.properties.pk] = f; });

    summits.features.forEach(feature => {
        const kcPk = feature.properties.kc;
        if (!kcPk) return;
        const col = byColPk[kcPk];
        if (!col) return;

        vectorSource.addFeature(new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat(col.geometry.coordinates)),
            name: col.properties.name,
            alt: col.properties.alt,
            pk: kcPk,
            type: 'col',
            key_for: col.properties.key_for,
            depth: col.properties.depth,
            confluence: col.properties.confluence,
        }));
    });

    return new ol.layer.Vector({
        source: vectorSource,
        style: styleFor,
    });
}


function buildConfluenceLayer(cols, visible = true) {
    const vectorSource = new ol.source.Vector();

    cols.features.forEach(feature => {
        const confluence = feature.properties.confluence;
        if (!confluence || confluence.lon == null) return;

        const colCoord = feature.geometry.coordinates;

        vectorSource.addFeature(confluenceLine(
            ol.proj.fromLonLat(colCoord),
            ol.proj.fromLonLat([confluence.lon, confluence.lat]),
        ));
    });

    const layer = new ol.layer.Vector({
        source: vectorSource,
        style: confluenceLineStyle,
        visible: visible,
    });
    layer.set('name', 'col_confluence');
    return layer;
}

function buildIsolationPointLayer(summits) {
    const vectorSource = new ol.source.Vector();

    summits.features.forEach(feature => {
        const ilp = feature.properties.ilp;
        if (!ilp || ilp.lon == null || ilp.lat == null) return;

        const hasParent = feature.properties.isolation_parent != null;

        vectorSource.addFeature(new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat([ilp.lon, ilp.lat])),
            name: ilp.name,
            alt: ilp.alt,
            type: 'isolation_point',
            has_parent: hasParent,
        }));
    });

    return new ol.layer.Vector({
        source: vectorSource,
        style: function(feature) {
            const base = styleFor(feature);
            if (!feature.get('has_parent')) {
                return [].concat(base).concat(new ol.style.Style({
                    image: new ol.style.Circle({
                        radius: 8,
                        fill: new ol.style.Fill({ color: 'rgba(0,0,0,0)' }),
                        stroke: new ol.style.Stroke({ color: '#ff0000', width: 2 }),
                    }),
                }));
            }
            return base;
        },
    });
}

let currentMode = 'prominence';

function summitStyleFor(feature) {
    if (feature.get('type') === 'summit'
            && currentMode === 'horizon'
            && !feature.get('horizon_parent')) {
        return styleFor({ get: k => k === 'type' ? 'horizon_king' : feature.get(k) });
    }
    return styleFor(feature);
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

function initGlobalMap(summitsUrl, riversUrl, colsUrl) {
    let map, lineageLayer, summitLayer;
    let summitsData, colsData;

    const routeToggle = document.getElementById('toggle-routing');

    let keyColLayer = null;
    let isolationPointLayer = null;

    // Hover highlight. Two sources rather than one, because the pieces belong at different
    // depths: the connecting line goes above the lineage lines but under the markers, while the
    // grown parent triangle has to sit *above* the summit layer or the peak's own marker would
    // be drawn on top of it and hide the effect.
    const highlightLineSource = new ol.source.Vector();
    const highlightPointSource = new ol.source.Vector();
    let summitsByPk = null;
    let colsByPk = null;
    let highlightedPk = null;
    let highlightFrame = null;

    // Advance the parent's grow-and-shine, repainting each frame. Style functions read
    // highlightProgress, so a render is what makes the new value visible.
    function animateHighlight(start) {
        const elapsed = performance.now() - start;
        setHighlightProgress(elapsed / HIGHLIGHT_MS);
        map.render();
        highlightFrame = elapsed < HIGHLIGHT_MS
            ? requestAnimationFrame(() => animateHighlight(start))
            : null;
    }

    function clearHighlight() {
        if (highlightFrame !== null) {
            cancelAnimationFrame(highlightFrame);
            highlightFrame = null;
        }
        highlightedPk = null;
        highlightLineSource.clear();
        highlightPointSource.clear();
    }

    function highlight(feature) {
        const pk = feature && feature.get('type') === 'summit' ? feature.get('pk') : null;
        // pointermove fires constantly; without this the sources would be rebuilt and the
        // animation restarted on every mouse position inside the same marker.
        if (pk === highlightedPk) return;
        clearHighlight();
        if (pk === null || !summitsByPk) return;
        highlightedPk = pk;

        const col = colsByPk[feature.get('kc')];
        if (col) {
            highlightPointSource.addFeature(new ol.Feature({
                geometry: new ol.geom.Point(ol.proj.fromLonLat(col.geometry.coordinates)),
                type: 'highlight_col',
            }));
        }

        // The parent of the hierarchy currently drawn, not always the prominence one: lighting up
        // a prominence parent while the map shows isolation lineage would contradict the lines.
        const mode = document.querySelector('input[name="tree"]:checked');
        const modeName = mode && mode.value;
        const parentAttr = lineageParentAttr(modeName) || 'prominence_parent';
        const parent = summitsByPk[feature.get(parentAttr)];
        if (!parent) return;

        highlightPointSource.addFeature(new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat(parent.geometry.coordinates)),
            type: 'highlight_parent',
            prom: parent.properties.prom,     // the triangle is sized from the parent's own marker
        }));

        // Same waypoint the lineage layer routes through, so the highlight lies exactly over the
        // line it is highlighting instead of cutting its own corner.
        const useRouting = routeToggle && routeToggle.checked;
        const waypoint = lineageWaypoint(modeName, feature.getProperties(), useRouting, colsByPk);
        const path = [feature.getGeometry().getCoordinates()];   // already in map projection
        if (waypoint) path.push(ol.proj.fromLonLat(waypoint));
        path.push(ol.proj.fromLonLat(parent.geometry.coordinates));

        highlightLineSource.addFeature(new ol.Feature({
            geometry: new ol.geom.LineString(path),
            type: 'highlight_line',
        }));

        setHighlightProgress(0);
        animateHighlight(performance.now());
    }

    function rebuildOverlayLayers() {
        if (keyColLayer) {
            map.removeLayer(keyColLayer);
            keyColLayer = null;
        }
        if (isolationPointLayer) {
            map.removeLayer(isolationPointLayer);
            isolationPointLayer = null;
        }

        const mode = document.querySelector('input[name="tree"]:checked').value;
        const useRouting = routeToggle && routeToggle.checked;

        if (mode === 'prominence' && useRouting) {
            keyColLayer = buildKeyColLayer(summitsData, colsData);
            keyColLayer.set('name', 'keycols');
            keyColLayer.setZIndex(Z_OVERLAY_POINTS);
            map.addLayer(keyColLayer);
        }
        if (mode === 'isolation' && useRouting) {
            isolationPointLayer = buildIsolationPointLayer(summitsData);
            isolationPointLayer.set('name', 'isolation_points');
            isolationPointLayer.setZIndex(Z_OVERLAY_POINTS);
            map.addLayer(isolationPointLayer);
        }
    }

    function rebuildLineage() {
        if (!map || !summitsData) return;
        currentMode = document.querySelector('input[name="tree"]:checked').value;
        const useRouting = routeToggle && routeToggle.checked;
        if (lineageLayer) map.removeLayer(lineageLayer);

        lineageLayer = buildLineageLayer(summitsData, colsData, currentMode, useRouting);
        lineageLayer.set('name', 'lineage');
        lineageLayer.setZIndex(Z_LINEAGE);
        map.addLayer(lineageLayer);
        rebuildOverlayLayers();
        // refresh summit layer style
        summitLayer.setStyle(summitStyleFor);
    }

    Promise.all([
        fetch(summitsUrl).then(r => r.json()),
        fetch(riversUrl).then(r => r.json()),
        fetch(colsUrl).then(r => r.json()),
    ]).then(([summits, rivers, cols]) => {
        summitsData = summits;
        colsData = cols;

        summitsByPk = {};
        summits.features.forEach(f => { summitsByPk[f.properties.pk] = f; });
        colsByPk = {};
        cols.features.forEach(f => { colsByPk[f.properties.pk] = f; });

        const { map: m, tileLayer, vectorLayer } = makeMap(summits, styleFor, [22, 49], 11,
                                                          highlight);
        map = m;
        summitLayer = vectorLayer;
        summitLayer.set('name', 'summits');
        renderProminenceLegend();

        // Both names start with "highlight" — that prefix is what featureAt() filters on, so
        // neither layer can ever be hit-tested and steal the hover from the peak underneath.
        [['highlight-lines', highlightLineSource, Z_HIGHLIGHT_LINE],
         ['highlight-marks', highlightPointSource, Z_HIGHLIGHT_MARK]].forEach(([name, source, z]) => {
            const layer = new ol.layer.Vector({ source: source, style: styleFor, zIndex: z });
            layer.set('name', name);
            map.addLayer(layer);
        });

        const opacitySlider = document.getElementById('map-opacity');
        if (opacitySlider) {
            opacitySlider.addEventListener('input', function() {
                tileLayer.setOpacity(this.value / 100);
            });
        }

        const riversLayer = buildRiversLayer(rivers);
        riversLayer.set('name', 'rivers');
        riversLayer.setZIndex(Z_RIVERS);
        map.addLayer(riversLayer);

        const riversToggle = document.getElementById('toggle-rivers');
        if (riversToggle) {
            riversToggle.addEventListener('change', function() {
                riversLayer.setVisible(this.checked);
            });
        }

        const colToggle = document.getElementById('toggle-col-confluence');
        if (colToggle) {
            colToggle.addEventListener('change', function() {
                colConfluenceLayer.setVisible(this.checked);
            });
        }
        let colConfluenceLayer = buildConfluenceLayer(cols, colToggle && colToggle.checked);
        colConfluenceLayer.set('name', 'col_confluence');
        colConfluenceLayer.setZIndex(Z_CONFLUENCE);
        map.addLayer(colConfluenceLayer);


        rebuildLineage();

        document.querySelectorAll('input[name="tree"]').forEach(r =>
            r.addEventListener('change', rebuildLineage)
        );
        if (routeToggle) routeToggle.addEventListener('change', rebuildLineage);
    });
}
