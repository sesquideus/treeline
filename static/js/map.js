// map_utils.js — OpenLayers helpers for mountains app

const SEGMENTS = 20;

const GREEN  = [39, 174, 96];
const YELLOW = [241, 196, 15];
const RED    = [192, 57, 43];
const BLUE   = [43, 57, 192];
const PURPLE = [142, 68, 173];

function interpolateColor(c1, c2, t) {
    return `rgb(${Math.round(c1[0]+(c2[0]-c1[0])*t)},${Math.round(c1[1]+(c2[1]-c1[1])*t)},${Math.round(c1[2]+(c2[2]-c1[2])*t)})`;
}

function denseCoords(coords) {
    const dense = [];
    for (let i = 0; i < coords.length - 1; i++) {
        for (let s = 0; s < SEGMENTS; s++) {
            const t = s / SEGMENTS;
            dense.push([
                coords[i][0] + (coords[i+1][0] - coords[i][0]) * t,
                coords[i][1] + (coords[i+1][1] - coords[i][1]) * t,
            ]);
        }
    }
    dense.push(coords[coords.length - 1]);
    return dense;
}

function segmentStyles(coords, c1, c2) {
    return coords.slice(0, -1).map((_, i) => new ol.style.Style({
        geometry: new ol.geom.LineString([coords[i], coords[i+1]]),
        stroke: new ol.style.Stroke({
            color: interpolateColor(c1, c2, i / (coords.length - 2)),
            width: 4,
        })
    }));
}

function gradientLine(feature, c1, c2) {
    return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()), c1, c2);
}

function dot(color, radius=6) {
    return new ol.style.Style({
        image: new ol.style.Circle({
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 1.5 }),
        })
    });
}

function summit(color, radius=6) {
    return new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 3,
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 1.5 }),
        })
    });
}

function ilp(color, radius=6) {
    return new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 4,
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 0.5 }),
        })
    });
}

function colMarker() {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">
        <circle cx="12" cy="12" r="10"
                fill="white"
                stroke="#2760ae"
                stroke-width="2.5"/>
        <text x="12" y="16"
              text-anchor="middle"
              font-size="14"
              font-weight="bold"
              fill="#2760ae">)(</text>
    </svg>`;
    return new ol.style.Style({
        image: new ol.style.Icon({
            src: 'data:image/svg+xml;utf8,' + encodeURIComponent(svg),
            anchor: [0.5, 0.5],
        })
    });
}


function dashedLine(color, width=3, dash=[8, 5]) {
    return new ol.style.Style({
        stroke: new ol.style.Stroke({ color, width, lineDash: dash })
    });
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
        view: new ol.View({ center: ol.proj.fromLonLat(coords), zoom: 10 }),
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

function withAlpha(color, alpha) {
    return `rgba(${color[0]},${color[1]},${color[2]},${alpha})`;
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

function buildLineageLayer(lineage, routeThroughCol) {
    const features = [];
    const summit = lineage.summit;
    const summitCoord = lonLatToMercator(summit.lon, summit.lat);

    // ancestors — bigger and more faded as we go up
    lineage.ancestors.forEach((ancestor, i) => {
        const alpha = 1;
        const radius = 6 + (i + 1);
        const coord = lonLatToMercator(ancestor.lon, ancestor.lat);

        const point = new ol.Feature({
            geometry: new ol.geom.Point(coord),
            name: `${ancestor.name} (${ancestor.alt} m)`,
            url: `/summits/${ancestor.pk}/`,
            featureType: 'ancestor',
        });
        point.setStyle(new ol.style.Style({
            image: new ol.style.RegularShape({
                points: 3,
                radius,
                fill: new ol.style.Fill({ color: withAlpha(BLUE, alpha) }),
                stroke: new ol.style.Stroke({ color: withAlpha([255,255,255], alpha), width: 1.5 }),
            })
        }));
        features.push(point);

        const prevCoord = i === 0
            ? summitCoord
            : lonLatToMercator(lineage.ancestors[i-1].lon, lineage.ancestors[i-1].lat);

        const lowerPeak = i === 0 ? lineage.summit : lineage.ancestors[i-1];

        let lineCoordsList;
        if (routeThroughCol && lowerPeak.kc) {
            const colCoord = lonLatToMercator(lowerPeak.kc.lon, lowerPeak.kc.lat);
            lineCoordsList = denseCoords([prevCoord, colCoord, coord]);

            const colFeature = new ol.Feature({
                geometry: new ol.geom.Point(colCoord),
                name: `${lowerPeak.kc.name || 'Key col'} (${lowerPeak.kc.alt} m)`,
                featureType: 'col',
            });
            colFeature.setStyle(colMarker());
            features.push(colFeature);
        } else {
            lineCoordsList = denseCoords([prevCoord, coord]);
        }


        const line = new ol.Feature({
            geometry: new ol.geom.LineString(lineCoordsList),
            featureType: 'ancestor_line',
        });
        line.setStyle(fadedSegmentStyles(lineCoordsList, BLUE, RED, alpha, 3));
        features.push(line);
    });

    // children — smaller triangles, faded lines
    lineage.children.forEach(child => {
        if (!child.lat || !child.lon) return;
        const childCoord = lonLatToMercator(child.lon, child.lat);

        const point = new ol.Feature({
            geometry: new ol.geom.Point(childCoord),
            name: `\u26f0 ${child.name} ${child.alt} m ${child.prom ? '\u2195 ' + Math.round(child.prom) + ' m' : ''}`,
            url: `/summits/${child.pk}/`,
            featureType: 'child',
        });
        point.setStyle(new ol.style.Style({
            image: new ol.style.RegularShape({
                points: 3,
                radius: 8,
                fill: new ol.style.Fill({ color: withAlpha(RED, 0.6) }),
                stroke: new ol.style.Stroke({ color: withAlpha([255,255,255], 0.6), width: 1.5 }),
            })
        }));
        features.push(point);

        let lineCoordsList;
        if (routeThroughCol && child.kc) {
            const colCoord = lonLatToMercator(child.kc.lon, child.kc.lat);
            lineCoordsList = denseCoords([childCoord, colCoord, summitCoord]);

            const colFeature = new ol.Feature({
                geometry: new ol.geom.Point(colCoord),
                name: `${child.kc.name || 'Key col'} (${child.kc.alt} m)`,
                featureType: 'col',
            });
            colFeature.setStyle(colMarker());
            features.push(colFeature);
        } else {
            lineCoordsList = denseCoords([childCoord, summitCoord]);
        }

        const line = new ol.Feature({
            geometry: new ol.geom.LineString(lineCoordsList),
            featureType: 'child_line',
        });
        line.setStyle(fadedSegmentStyles(lineCoordsList, RED, PURPLE, 1, 2));
        features.push(line);
    });

    return new ol.layer.Vector({
        source: new ol.source.Vector({ features }),
    });
}
