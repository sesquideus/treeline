// Layer draw order. Layers are added and removed as modes change, so ordering is pinned
// with an explicit zIndex rather than left to insertion order. Summits go on top: they are
// the click targets, and a marker hidden under a lineage line cannot be hit.
const Z_RANGES         = 5;    // backdrop: under everything, and never a click target
const Z_RIVERS         = 10;
const Z_CONFLUENCE     = 15;
const Z_LINEAGE        = 20;
const Z_HIGHLIGHT_LINE = 25;   // over the lineage it traces, under the markers it connects
const Z_OVERLAY_POINTS = 30;
const Z_SUMMITS        = 40;
const Z_HIGHLIGHT_MARK = 45;   // above the summits: the grown parent replaces its own marker

// Where the global map opens. Slovakia is where the data is dense — the collection reaches
// round the world now, and fitting all of it opens on a view nothing can be read from. As an
// extent rather than a centre and zoom, so the whole country is in frame on any window.
const HOME_EXTENT = [16.83, 47.73, 22.57, 49.61];   // lon/lat, Slovakia

// What the reader had on screen last time, carried in the URL fragment:
//
//     #map=11.25/49.16451/20.13403&mode=isolation&cols=1
//
// The position keeps OpenStreetMap's "#map=<zoom>/<lat>/<lon>" shape so the format is
// recognisable and a pasted link lands where it says; the rest of the controls ride
// alongside it as ordinary key=value pairs.
//
// The fragment rather than query parameters, deliberately: it never reaches the server, so
// it cannot vary a cached page or a JSON endpoint, needs no Django view to know about it,
// and stays out of the way of the real query parameters the list pages use for filtering
// and ordering.
//
// Only what differs from the defaults is written, so the common case stays short and a
// reader who has changed nothing gets a clean "#map=..." rather than a wall of settings.
const MAP_MODES = ['prominence', 'isolation', 'slope', 'horizon'];
const MAP_TOGGLES = ['routing', 'rivers', 'cols', 'ranges'];
const MAP_DEFAULTS = {
    mode: 'prominence',
    routing: true,       // #toggle-routing
    rivers: true,        // #toggle-rivers
    cols: false,         // #toggle-col-confluence
    ranges: false,       // #toggle-ranges
    opacity: 40,         // #map-opacity, per cent
};

const MAP_POSITION = /^(-?[\d.]+)\/(-?[\d.]+)\/(-?[\d.]+)$/;

// Hand-rolled rather than `URLSearchParams`, which is a browser API and not part of the
// language — this way the parsing can be run by the tests (see mountains/test_js_runtime.py)
// rather than only read.
function parseFragment(hash) {
    const params = {};
    for (const pair of String(hash || '').replace(/^#/, '').split('&')) {
        const at = pair.indexOf('=');
        if (at > 0) params[pair.slice(0, at)] = pair.slice(at + 1);
    }
    return params;
}

function parseMapPosition(value) {
    const match = MAP_POSITION.exec(value || '');
    if (!match) return null;
    const [zoom, lat, lon] = match.slice(1).map(Number);
    // Anything outside these is either a typo or a different app's fragment; opening on the
    // home extent beats opening on empty ocean at zoom 400.
    if (![zoom, lat, lon].every(Number.isFinite)) return null;
    if (zoom < 0 || zoom > 22 || lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
    return { zoom, lat, lon };
}

function formatMapPosition(zoom, lat, lon) {
    // Zoom keeps two decimals because OpenLayers zooms fractionally; the position keeps
    // five, which is about a metre — more would be recording mouse jitter.
    return `${zoom.toFixed(2)}/${lat.toFixed(5)}/${lon.toFixed(5)}`;
}

// Every unrecognised or out-of-range value falls back to its default rather than rejecting
// the fragment wholesale: a stale link with one obsolete setting should still take the
// reader to the right place.
function parseMapState(hash) {
    const params = parseFragment(hash);
    const state = Object.assign({}, MAP_DEFAULTS);

    state.position = parseMapPosition(params.map);
    if (MAP_MODES.indexOf(params.mode) >= 0) state.mode = params.mode;
    for (const name of MAP_TOGGLES) {
        if (params[name] === '0' || params[name] === '1') state[name] = params[name] === '1';
    }
    const opacity = Number(params.opacity);
    if (params.opacity !== undefined && Number.isFinite(opacity)
            && opacity >= 0 && opacity <= 100) {
        state.opacity = opacity;
    }
    return state;
}

function formatMapState(state) {
    const parts = [];
    if (state.position) {
        const { zoom, lat, lon } = state.position;
        parts.push(`map=${formatMapPosition(zoom, lat, lon)}`);
    }
    if (state.mode !== MAP_DEFAULTS.mode) parts.push(`mode=${state.mode}`);
    for (const name of MAP_TOGGLES) {
        if (state[name] !== MAP_DEFAULTS[name]) parts.push(`${name}=${state[name] ? 1 : 0}`);
    }
    if (state.opacity !== MAP_DEFAULTS.opacity) parts.push(`opacity=${state.opacity}`);
    return parts.length ? '#' + parts.join('&') : '';
}

// Country flags for a popup caption, same images the tables use, to the right of the name.
// Wrapped in one nowrap span so the caption never breaks between the name and its flags, or
// in the middle of a pair.
function flagsHtml(codes) {
    if (!codes || !codes.length) return '';
    const images = codes.map(code =>
        `<img class="flag" src="https://flagpedia.net/data/flags/mini/${code}.png" alt="${code}">`
    ).join('');
    return `<span class="flags">${images}</span>`;
}

// Detail-page URLs. The source of truth is mountains/urls.py — 'summit-detail', 'col' and
// 'river-detail'; kept here as three one-liners rather than reversed per object server-side,
// which would be thousands of reverse() calls on the flat endpoints.
const summitUrl = pk => pk != null ? `/summit/${pk}/` : null;
const colUrl    = pk => pk != null ? `/col/${pk}/` : null;
const riverUrl  = pk => pk != null ? `/river/${pk}/` : null;
const pointUrl  = pk => pk != null ? `/point/${pk}/` : null;

// Every reference to another object is a link, styled with the site's object classes
// (`a.mountain`, `a.col`, `a.river` in main.css) so it carries the same colour and ⛰ ∪ 〰
// glyph as {% object_link %} does in the templates. Unlinkable labels stay plain text.
function objectLink(kind, url, label) {
    if (label == null) return null;
    return url ? `<a class="${kind}" href="${url}">${label}</a>` : label;
}

// The caption links to the feature's own detail page. The h3 already carries the object
// class, so the anchor inside it inherits the colour and adds no second glyph.
function popupCaption(cls, url, name, codes) {
    const label = url ? `<a href="${url}">${name}</a>` : name;
    return `<h3 class="${cls}">${label} ${flagsHtml(codes)}</h3>`;
}

// A popup body is one table; a section is a caption cell down its left edge, spanning the
// section's rows, so the group is named without spending a row on the name. Labels and
// values keep the same two columns from the first section to the last. A section with no
// rows left in it is dropped rather than rendered as a caption with nothing beside it.
function popupSection(caption, rows) {
    const cells = rows.filter(Boolean);
    if (!cells.length) return '';
    // A section that is a single unlabelled line has no label column to speak of: the
    // caption takes it, and the value stays in the column every other section's values are
    // in. Anywhere else the caption spans the section's rows instead.
    const lone = cells.length === 1 && cells[0].startsWith(MERGED_ROW);
    if (lone) {
        const spine = `<th class="section" colspan="2">${caption}</th>`;
        return cells[0]
            .replace('<td colspan="2"', '<td')
            .replace('<tr class="merged">', `<tr class="merged">${spine}`);
    }
    const spine = `<th class="section" rowspan="${cells.length}">${caption}</th>`;
    return cells
        .map((row, i) => i === 0 ? row.replace('<tr>', `<tr>${spine}`) : row)
        .join('');
}

function popupTable(sections) {
    const body = sections.filter(Boolean).join('');
    return body ? `<table class="tooltip">${body}</table>` : '';
}

// The opening of a row that carries no label of its own — one of the merged lines, whose
// figures and arrows say what they are. `popupSection()` recognises it by this prefix.
const MERGED_ROW = '<tr class="merged">';

function popupRow(label, value, cls) {
    if (value == null) return '';
    const attrs = cls ? ` class="${cls}"` : '';
    // Without a label the value takes the label column too, rather than leaving an empty
    // header cell beside it.
    return label
        ? `<tr><th>${label}</th><td${attrs}>${value}</td></tr>`
        : `${MERGED_ROW}<td colspan="2"${attrs}>${value}</td></tr>`;
}

// The unit is CSS (`.altitude::after` and friends), so these emit bare numbers. `?` keeps a
// row that is part of the feature's identity visible even when the value is missing.
const asAltitude = m => m != null ? m.toFixed(1) : null;
const asKm       = m => m != null ? (m / 1000).toFixed(3) : null;
// A gradient, a height difference against something else, or an angle above the horizon can
// go either way, so the sign is always shown — as `slope` and `diff_altitude` do in the
// mountain list. A distance or an altitude is never negative and carries no sign.
const signed     = text => text != null && !text.startsWith('-') ? `+${text}` : text;
const asSlope    = s => signed(s != null ? (s * 1000).toFixed(2) : null);
const asDegrees  = a => signed(a != null ? (a * 180 / Math.PI).toFixed(3) : null);
const orQuery    = v => v != null ? v : '?';

// "48.12345° N, 17.25346° E" — a coordinate pair reads as one fact, so it goes in one cell.
// The degree marks are in the text rather than from `.angle::after`, which would leave a
// stray one at the end of the pair.
function positionValue(lon, lat) {
    if (lon == null || lat == null) return null;
    return `${Math.abs(lat).toFixed(5)}°&nbsp;${lat >= 0 ? 'N' : 'S'}, `
         + `${Math.abs(lon).toFixed(5)}°&nbsp;${lon >= 0 ? 'E' : 'W'}`;
}

// The feature's own position, taken from its geometry — in the view projection, and only a
// point has one — rather than from the payload, so it is there before any detail arrives.
function featurePosition(feature) {
    const geometry = feature.getGeometry();
    if (!geometry || geometry.getType() !== 'Point') return null;
    return positionValue(...ol.proj.toLonLat(geometry.getCoordinates()));
}

// "Sairecabur 5991.0 m" — an altitude belongs beside the name it qualifies rather than on a
// row of its own. No brackets: the unit already tells the reader what the number is. Written
// out, the cell no longer being an `.altitude` one.
// A summit as a river popup names it: linked, with its altitude. Null when the river does
// not name one, which `popupRow` turns into no row at all.
function summitLabel(summit) {
    if (!summit) return null;
    return withAltitude(objectLink('mountain', summitUrl(summit.pk), summit.name), summit.alt);
}

// A named point, likewise — but through `/point/<pk>/`, because a watershed high point need
// not be a summit at all. Kept separate from `summitLabel` rather than made to share it: the
// two carry different pk spaces, and `summitUrl` handed a NamedPoint pk builds a URL that
// resolves to an unrelated mountain instead of 404ing.
function pointLabel(point) {
    if (!point) return null;
    return withAltitude(objectLink('mountain', pointUrl(point.pk), point.name), point.alt);
}


function withAltitude(label, alt) {
    const altitude = asAltitude(alt);
    return altitude != null ? `${label} ${altitude}&nbsp;m` : label;
}

// "Veľký kopec ↘ ↗ Kráľova hoľa" — the peak a col belongs to and the higher one across it,
// the arrows tracing the way down into the saddle and up out of it. Either half may be
// unknown, in which case the arrows go with it.
function betweenPeaks(feature) {
    const minor = objectLink('mountain', summitUrl(feature.get('key_for_pk')),
                             feature.get('key_for'));
    const major = objectLink('mountain', summitUrl(feature.get('major_pk')),
                             feature.get('major'));
    if (minor && major) return `${minor}&nbsp;↘&nbsp;↗&nbsp;${major}`;
    return minor ?? major;
}

// "11.500 km → western ridge of Babiná", "+5.87 m/km ↗ Sairecabur 5991.0 m": the figure that
// defines a relation and the thing it points at, on one line. The spine already says which
// relation this is, so the row carries no label, and the unit is written out — a cell holding
// two kinds of value cannot take it from `.distance::after` and friends.
function towardsRow(value, target, arrow = '→') {
    if (value == null && target == null) return '';
    if (target == null) return popupRow('', value, 'name');
    return popupRow('', value != null ? `${value}&nbsp;${arrow} ${target}` : target, 'name');
}

const kmText    = m => { const km = asKm(m); return km != null ? `${km}&nbsp;km` : null; };
const slopeText = s => { const v = asSlope(s); return v != null ? `${v}&nbsp;m/km` : null; };
const angleText = a => { const v = asDegrees(a); return v != null ? `${v}°` : null; };

// The summit a relation points at, with its altitude beside it.
function relationTarget(relation) {
    return withAltitude(
        objectLink('mountain', summitUrl(relation.pk), relation.name ?? 'unnamed'),
        relation.alt);
}

// The prominence band as the marker shows it: a triangle in the band's colour, which is the
// same value `styles.js` fills the summit marker with. Colour never carries the band on its
// own — the band's name is the triangle's tooltip, and the page legend spells the ramp out.
function bandMark(prominence) {
    const band = prominenceBand(prominence);
    return `<span class="band" style="color: ${band.colour}" title="${band.label}">▲</span>`;
}


function popupHtml(feature) {
    if (!feature) return '';
    switch (feature.get('type')) {
        case 'summit': {
            const keyCol = feature.get('key_col');
            const parent = feature.get('parent');
            const ilp = feature.get('ilp');
            const nhn = feature.get('nhn');
            const slope = feature.get('slope');
            const horizon = feature.get('horizon');
            // The nearest higher point and the summit it belongs to read as one phrase —
            // "western ridge of Babiná" — and collapse to whichever half is known.
            const ilpParent = ilp
                ? objectLink('mountain', summitUrl(feature.get('isolation_parent')), ilp.parent)
                : null;
            const nhnPhrase = ilp && ilp.name && ilpParent
                ? `${ilp.name} of ${ilpParent}`
                : (ilp && ilp.name) ?? ilpParent;
            return popupCaption('mountain', summitUrl(feature.get('pk')),
                                feature.get('name') ?? 'unnamed peak', feature.get('countries'))
                + popupTable([
                    popupSection('summit', [
                        popupRow('position', featurePosition(feature)),
                        popupRow('altitude', orQuery(asAltitude(feature.get('alt'))), 'altitude'),
                        popupRow('prominence', bandMark(feature.get('prom'))
                                 + orQuery(asAltitude(feature.get('prom'))), 'altitude'),
                    ]),
                    // One line, like the four relations below it. No drop, because a key
                    // col's drop *is* the prominence the summit section already gives, and
                    // no gradient — that is the slope section's business.
                    keyCol ? popupSection('key col', [
                        towardsRow(kmText(keyCol.dist), withAltitude(
                            objectLink('col', colUrl(keyCol.pk), keyCol.name ?? 'unnamed'),
                            keyCol.alt)),
                    ]) : '',
                    parent ? popupSection('parent',
                                          [towardsRow(kmText(parent.dist),
                                                      relationTarget(parent))]) : '',
                    ilp ? popupSection('isolation',
                                       [towardsRow(kmText(ilp.dist), nhnPhrase)]) : '',
                    // The neighbour itself, and peak to peak — the isolation above is
                    // measured to the ground, which is usually short of the summit.
                    nhn ? popupSection('NHN',
                                       [towardsRow(kmText(nhn.dist),
                                                   relationTarget(nhn))]) : '',
                    slope ? popupSection('slope',
                                         [towardsRow(slopeText(slope.slope),
                                                     relationTarget(slope), '↗')]) : '',
                    horizon ? popupSection('horizon',
                                           [towardsRow(angleText(horizon.angle),
                                                       relationTarget(horizon), '↗')]) : '',
                ]);
        }
        case 'col': {
            const confluence = feature.get('confluence');
            const river = feature.get('river');
            const riverLink = river
                ? objectLink('river', riverUrl(river.pk), river.name)
                : null;
            const parentRiverLink = river && river.parent
                ? objectLink('river', riverUrl(river.parent_pk), river.parent)
                : null;
            return popupCaption('col', colUrl(feature.get('pk')),
                                feature.get('name') ?? 'unnamed col', feature.get('countries'))
                + popupTable([
                    popupSection('col', [
                        popupRow('altitude', orQuery(asAltitude(feature.get('alt'))), 'altitude'),
                        // The two slopes that meet here: down from the peak whose key col
                        // this is, up to the higher ground it hangs off.
                        popupRow('between', betweenPeaks(feature), 'name'),
                        popupRow('depth', asAltitude(feature.get('depth')), 'altitude'),
                    ]),
                    popupSection('confluence', [
                        // The river the col drains into, and the one that receives it in turn.
                        popupRow('river', parentRiverLink
                            ? `${riverLink}&nbsp;→&nbsp;${parentRiverLink}`
                            : riverLink, 'name'),
                        popupRow('altitude', confluence ? asAltitude(confluence.alt) : null, 'altitude'),
                        popupRow('distance', confluence ? asKm(confluence.dist) : null, 'distance'),
                        popupRow('position', confluence
                            ? positionValue(confluence.lon, confluence.lat)
                            : null),
                    ]),
                ]);
        }
        case 'river': {
            const parent = feature.get('parent');
            const source = feature.get('source');
            const mouth = feature.get('mouth');
            const aboveSource = feature.get('source_summit');
            const watershed = feature.get('watershed_high_point');
            // The bank it joins on, drawn from the same sprite the river pages use — the
            // ids are the contract, see `River.MOUTH_SIDE_MARKS`. A picture rather than an
            // arrow because "left bank" is named looking downstream, which is not the side
            // it lands on in a drawing, and no arrow can say that without a convention.
            const side = feature.get('mouth_side');
            const sideMark = side
                ? `<abbr title="${side.title}"><svg class="mouth-side" role="img">` +
                  `<use href="#mouth-side-${side.code}"></use></svg></abbr>&nbsp;`
                : '';
            return popupCaption('river', riverUrl(feature.get('pk')),
                                feature.get('name') ?? 'unknown', null)
                + popupTable([
                    source ? popupSection('source', [
                        popupRow('altitude', asAltitude(source.alt), 'altitude'),
                        popupRow('position', positionValue(source.lon, source.lat)),
                        // The peak the source runs off, which is not the watershed's
                        // highest below and often not even in the same group.
                        popupRow('below', summitLabel(aboveSource), 'name'),
                    ]) : '',
                    // Where it ends and what receives it belong together: the river it
                    // flows into leads, then the mouth's own altitude and position. No
                    // `mouth ?` guard — the first row stands on its own, and the section
                    // drops itself when every row in it is empty.
                    popupSection('mouth', [
                        popupRow('flows into', parent
                            ? sideMark + objectLink('river', riverUrl(parent.id), parent.name)
                            : '—', 'name'),
                        popupRow('altitude', mouth ? asAltitude(mouth.alt) : null, 'altitude'),
                        popupRow('position', mouth ? positionValue(mouth.lon, mouth.lat) : null),
                    ]),
                    // The ground it drains, rather than the channel: the highest summit
                    // inside the watershed, linked like every other object reference here.
                    // Last, because source and mouth are the river itself and this is the
                    // country around it. One unlabelled row, so `popupSection` gives the
                    // caption the label column rather than spending two on naming one line
                    // — the same shape the summit popup's single-line sections take.
                    popupSection('watershed HP', [
                        popupRow('', pointLabel(watershed), 'name'),
                    ]),
                ]);
        }
        default:
            return '';
    }
}

// `onHover` is optional and receives the feature under the cursor (or null). The tooltip is
// wired up here; anything else that should react to hovering — the key col and parent
// highlight, for one — goes through the callback rather than adding a second pointermove
// listener, so the hit test still runs once per mouse position.
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

    // Fitting the data is for the single-summit maps, which are handed a centre but no zoom
    // and would otherwise open at an arbitrary scale. The global map passes a zoom and keeps
    // it: its collection now spans the world, so fitting it would open on a view where every
    // marker sits on top of its neighbours.
    if (zoom === undefined) {
        vectorLayer.getSource().once('change', function() {
            const extent = vectorLayer.getSource().getExtent();
            if (extent && isFinite(extent[0])) {
                map.getView().fit(extent, { padding: [60, 60, 60, 60] });
            }
        });
    }

    const popup = document.createElement('div');
    popup.className = 'map-popup';
    // Chrome only — the body's own type and spacing live in `.map-popup` in main.css.
    // `pointer-events: none`: the popup never stands between the cursor and the map, so it
    // goes the moment the pointer leaves the feature. Its links stay clickable by tap (see
    // main.css), which is how they are reached on a touch screen; with a mouse they are
    // there to say what the popup is pointing at.
    popup.style.cssText ='background:#fff;padding:5px 8px;border-radius:4px;font:13px/1.3 sans-serif;pointer-events:none;box-shadow:0 1px 4px rgba(0,0,0,0.3);';
    document.body.appendChild(popup);

    const overlay = new ol.Overlay({ element: popup, positioning: 'bottom-center',
                                     offset: [0, -10] });
    map.addOverlay(overlay);

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
            { layerFilter: layer => {
                     const name = layer.get('name') || '';
                     // Ranges are backdrop: a polygon covering the viewport would otherwise
                     // win every hit test and make the summits under it unclickable.
                     return !name.startsWith('highlight') && name !== 'ranges';
                 } });
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

    // Showing and hiding follow the cursor with nothing in between — no timer, no state to
    // reconcile: whatever is under the pointer right now is what the popup describes.
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

    // Touch devices have no hover at all, so a tap still opens the popup — and, there being
    // no pointermove to follow, leaves it open for a second tap on one of its links.
    map.on('click', function(e) {
        hovered(featureAt(e.pixel), e.coordinate);
    });

    const opacitySlider = document.getElementById('map-opacity');
    if (opacitySlider) {
        opacitySlider.addEventListener('input', function() {
            tileLayer.setOpacity(this.value / 100);
        });
    }

    // Popup contents can arrive after the popup is already open — properties are merged onto
    // the feature when its viewport detail lands — so the caller needs a way to re-render
    // what is on screen. `described` is the feature the current body was built from.
    function refreshPopup() {
        if (!described) return;
        const html = popupHtml(described);
        if (html) popup.innerHTML = html;
    }

    return { map, tileLayer, vectorLayer, refreshPopup };
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
// The sisters' lines are the same run of pink, lightened: they answer a question about the
// col under the cursor, and its own line should stay the strongest thing on screen.
const CONFLUENCE_SISTER_A        = [255, 170, 255, 1];
const CONFLUENCE_SISTER_B        = [255, 225, 255, 1];
// The rivers are water, and they are drawn over the blue of the rivers layer — pink there
// read as another col line. A light blue lifts the two channels out of the darker course
// beneath them without leaving the hue the map already uses for rivers.
const CONFLUENCE_RIVER_COLOUR    = 'rgba(110, 205, 255, 0.95)';

// One definition of the pink col → confluence line, shared by the layer the toggle switches
// on and the one a hovered col draws for itself, so the two cannot drift apart.
function confluenceLineStyle(feature) {
    return segmentStyles(
        denseCoords(feature.getGeometry().getCoordinates()),
        CONFLUENCE_COLOUR_A,
        CONFLUENCE_COLOUR_B,
    );
}

// The course of a river, drawn over the blue of the rivers layer in the confluence pink, so
// a hovered col shows which water it drains into. Solid and a little heavier than the
// col → confluence lines, which are a gradient: a channel is one thing, not a direction.
function confluenceRiverStyle() {
    return new ol.style.Style({
        stroke: new ol.style.Stroke({ color: CONFLUENCE_RIVER_COLOUR, width: 5 }),
        zIndex: Z_LINE,
    });
}

function confluenceSisterStyle(feature) {
    return segmentStyles(
        denseCoords(feature.getGeometry().getCoordinates()),
        CONFLUENCE_SISTER_A,
        CONFLUENCE_SISTER_B,
    );
}

function confluenceGroupStyle(feature) {
    switch (feature.get('type')) {
        case 'confluence_river': return confluenceRiverStyle();
        case 'confluence_sister': return confluenceSisterStyle(feature);
        default: return styleFor(feature);      // the rings on the sister cols themselves
    }
}

// Both endpoints in the view projection. `type` picks the style: the hovered col's own line
// is a `confluence_line`, a sister's the lighter `confluence_sister`.
function confluenceLine(colCoord, confluenceCoord, type = 'confluence_line') {
    return new ol.Feature({
        geometry: new ol.geom.LineString([colCoord, confluenceCoord]),
        type: type,
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
            // The whole property set rather than a hand-picked subset. The popup reads
            // `countries` and `river` too, and anything Col.to_dict() grows next; a key
            // missing here shows up only as a row silently absent from the popup.
            ...col.properties,
            geometry: new ol.geom.Point(ol.proj.fromLonLat(col.geometry.coordinates)),
            pk: kcPk,
            type: 'col',
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

// A summit with no parent in the hierarchy on show gets a glyph instead of a line going
// nowhere — and which glyph says which kind of nothing it is. The computation stamps
// `slope_computed` / `horizon_computed` whether or not it finds a parent, so a missing one
// with the stamp means "looked, there is none" (crown, empty set) and without it "nobody has
// looked" (question mark). `top` settles the slope case on its own: nothing is higher than
// that summit, so no slope parent can exist whether the action has run or not.
const ROOTLESS_MARK = {
    horizon: {
        parent: 'horizon_parent', computed: 'horizon_computed',
        root: 'horizon_king', missing: 'horizon_unknown',
    },
    slope: {
        parent: 'slope_parent', computed: 'slope_computed',
        root: 'slope_root', missing: 'slope_unknown',
    },
};

function summitStyleFor(feature) {
    if (feature.get('type') !== 'summit') return styleFor(feature);
    const mark = ROOTLESS_MARK[currentMode];
    if (!mark || feature.get(mark.parent)) return styleFor(feature);
    const settled = feature.get(mark.computed) || feature.get('top');
    const type = settled ? mark.root : mark.missing;
    return styleFor({ get: k => k === 'type' ? type : feature.get(k) });
}

// Range polygons, drawn beneath everything else. Ranges without a boundary are dropped
// server-side, so an entirely nominal hierarchy yields an empty collection and no layer
// content — not an error.
function buildRangesLayer(ranges) {
    const vectorSource = new ol.source.Vector();
    const format = new ol.format.GeoJSON();

    vectorSource.addFeatures(format.readFeatures(ranges, {
        featureProjection: 'EPSG:3857',
    }));

    const layer = new ol.layer.Vector({
        source: vectorSource,
        style: styleFor,
    });
    // Hit testing takes the topmost feature, and a range polygon covers the whole viewport;
    // naming it 'highlight…' would be a lie, so instead the layer opts out of hit testing
    // entirely and summits underneath stay clickable.
    layer.set('name', 'ranges');
    layer.setZIndex(Z_RANGES);
    return layer;
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
                // Thin: there are hundreds of them, and the highlight below has to be able
                // to sit on top of one and be seen.
                width: 2,
            }),
        }),
    });
}

function initGlobalMap(summitsUrl, riversUrl, colsUrl, summitsDetailUrl, colsDetailUrl,
                       rangesUrl) {
    let map, lineageLayer, summitLayer, refreshPopup;
    let summitsData, colsData;

    const routeToggle = document.getElementById('toggle-routing');

    let keyColLayer = null;
    let isolationPointLayer = null;
    let keyColFeatureByPk = {};
    let summitFeatureByPk = {};
    let colsByRiver = {};          // river pk → the cols that drain into it
    let riversByPk = {};
    let summitByKeyCol = {};       // col pk → the summit whose key col it is

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

        // The peak itself, grown: the marker under the cursor answers the hover before the
        // eye has found the parent at the other end of the line.
        highlightPointSource.addFeature(new ol.Feature({
            geometry: new ol.geom.Point(feature.getGeometry().getCoordinates()),
            type: 'highlight_summit',
            prom: feature.get('prom'),
        }));

        // The parent of the hierarchy currently drawn, not always the prominence one: lighting up
        // a prominence parent while the map shows isolation lineage would contradict the lines.
        const mode = document.querySelector('input[name="tree"]:checked');
        const modeName = mode && mode.value;
        const parentAttr = lineageParentAttr(modeName) || 'prominence_parent';
        const useRouting = routeToggle && routeToggle.checked;

        // Whatever the lineage routes through gets a ring: the key col in prominence mode,
        // the nearest higher point in isolation mode when one is recorded, nothing in the
        // other two — and nothing at all with routing off, where the line runs straight to
        // the parent and a ring would sit on empty ground. `lineageWaypoint()` decides all
        // of that already, and it is the same call the highlight line uses below, so the
        // ring cannot land off the line.
        const waypoint = lineageWaypoint(modeName, feature.getProperties(), useRouting, colsByPk);
        if (waypoint) {
            highlightPointSource.addFeature(new ol.Feature({
                geometry: new ol.geom.Point(ol.proj.fromLonLat(waypoint)),
                type: 'highlight_waypoint',
            }));
        }

        // The parent of the hierarchy on show — prominence, isolation, slope or horizon.
        const parent = summitsByPk[feature.get(parentAttr)];
        if (!parent) return;

        highlightPointSource.addFeature(new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat(parent.geometry.coordinates)),
            type: 'highlight_parent',
            prom: parent.properties.prom,     // the triangle is sized from the parent's own marker
        }));

        // Through the same waypoint, so the highlight lies exactly over the line it is
        // highlighting instead of cutting its own corner.
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

    // --- popup detail ------------------------------------------------------------------
    // The three endpoints above serve skeletons: position, name, prominence and the ids the
    // joins below need. Altitudes, countries, key col, parent, isolation and river are read
    // by the popup of one feature at a time, so they are fetched for the current viewport
    // (mountains/views/viewport.py) and merged onto the features already loaded — the popup
    // builder reads feature properties and does not care when they arrived.
    const DETAIL_LIMIT = 300;
    const DETAIL_DEBOUNCE_MS = 250;
    const detailHeld = { summit: new Set(), col: new Set() };
    const detailUrl = { summit: summitsDetailUrl, col: colsDetailUrl };
    let detailTimer = null;
    let detailExtent = null;       // the extent already asked for, in EPSG:4326

    function applyDetail(kind, payload) {
        Object.entries(payload).forEach(([key, detail]) => {
            const pk = Number(key);
            detailHeld[kind].add(pk);
            if (kind === 'summit') {
                if (summitFeatureByPk[pk]) summitFeatureByPk[pk].setProperties(detail);
                if (summitsByPk[pk]) Object.assign(summitsByPk[pk].properties, detail);
            } else {
                // Merged into the raw GeoJSON as well: buildKeyColLayer() spreads those
                // properties into fresh features every time the overlay is rebuilt.
                if (colsByPk[pk]) Object.assign(colsByPk[pk].properties, detail);
                if (keyColFeatureByPk[pk]) keyColFeatureByPk[pk].setProperties(detail);
            }
        });
        if (refreshPopup) refreshPopup();      // the open popup may be one of them
    }

    function fetchDetail(kind, query) {
        if (!detailUrl[kind]) return;
        fetch(`${detailUrl[kind]}?${query}`)
            .then(r => r.ok ? r.json() : {})
            .then(payload => applyDetail(kind, payload))
            .catch(() => {});                  // detail is an enrichment, never load-critical
    }

    // A feature hovered before its viewport batch arrived — one pk, straight away.
    function requestDetailFor(feature) {
        if (!feature) return;
        const kind = feature.get('type');
        if (kind !== 'summit' && kind !== 'col') return;
        const pk = feature.get('pk');
        if (pk == null || detailHeld[kind].has(pk)) return;
        detailHeld[kind].add(pk);              // in flight: do not ask again on the next move
        fetchDetail(kind, `pks=${pk}`);
    }

    function requestViewportDetail() {
        if (!map) return;
        const extent = ol.proj.transformExtent(
            map.getView().calculateExtent(map.getSize()), 'EPSG:3857', 'EPSG:4326');
        // Zooming in asks for a subset of what has already been asked for.
        if (detailExtent && ol.extent.containsExtent(detailExtent, extent)) return;
        detailExtent = extent;
        const bbox = extent.map(v => v.toFixed(5)).join(',');
        ['summit', 'col'].forEach(kind =>
            fetchDetail(kind, `bbox=${bbox}&limit=${DETAIL_LIMIT}`));
    }

    // --- remembering the view -------------------------------------------------------
    //
    // The DOM half of the fragment handling; the parsing and formatting above are pure so
    // that the tests can run them.

    const CONTROL_IDS = {
        routing: 'toggle-routing',
        rivers: 'toggle-rivers',
        cols: 'toggle-col-confluence',
        ranges: 'toggle-ranges',
    };

    function readMapState() {
        const [lon, lat] = ol.proj.toLonLat(map.getView().getCenter());
        const mode = document.querySelector('input[name="tree"]:checked');
        const opacity = document.getElementById('map-opacity');
        const state = {
            position: { zoom: map.getView().getZoom(), lat: lat, lon: lon },
            mode: mode ? mode.value : MAP_DEFAULTS.mode,
            opacity: opacity ? Number(opacity.value) : MAP_DEFAULTS.opacity,
        };
        for (const name of MAP_TOGGLES) {
            const box = document.getElementById(CONTROL_IDS[name]);
            state[name] = box ? box.checked : MAP_DEFAULTS[name];
        }
        return state;
    }

    // `notify` dispatches the same events a click would, so the listeners already wired to
    // these controls do the actual work — the alternative is a second implementation of
    // every toggle that would drift from the first. Off while the map is still being built,
    // because the layers read the controls directly as they are constructed.
    function applyMapControls(state, notify) {
        const radio = document.querySelector(`input[name="tree"][value="${state.mode}"]`);
        if (radio && !radio.checked) {
            radio.checked = true;
            if (notify) radio.dispatchEvent(new Event('change', { bubbles: true }));
        }
        for (const name of MAP_TOGGLES) {
            const box = document.getElementById(CONTROL_IDS[name]);
            if (box && box.checked !== state[name]) {
                box.checked = state[name];
                if (notify) box.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }
        const opacity = document.getElementById('map-opacity');
        if (opacity && Number(opacity.value) !== state.opacity) {
            opacity.value = state.opacity;
            if (notify) opacity.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }

    function applyMapPosition(position) {
        if (!position) return;
        map.getView().setCenter(ol.proj.fromLonLat([position.lon, position.lat]));
        map.getView().setZoom(position.zoom);
    }

    // `replaceState`, never `pushState` and never assigning to `location.hash`: both of
    // those would leave a history entry per pan, and three minutes of browsing would bury
    // whatever page the reader arrived from under a hundred near-identical map positions.
    // (`replaceState` also fires no `hashchange`, which is what keeps the listener below
    // from answering our own writes.)
    function rememberMapState() {
        if (!window.history || !window.history.replaceState) return;
        const fragment = formatMapState(readMapState());
        window.history.replaceState(
            null, '', fragment || window.location.pathname + window.location.search);
    }

    function scheduleViewportDetail() {
        clearTimeout(detailTimer);
        detailTimer = setTimeout(requestViewportDetail, DETAIL_DEBOUNCE_MS);
    }

    function onHover(feature) {
        highlight(feature);
        showConfluenceGroup(feature);
        requestDetailFor(feature);
    }

    // Hovering a col lights up the whole confluence, not just its own line down to it: every
    // sister col — the cols draining into the same river — draws its line to the same mouth,
    // and the two rivers meeting there are traced in the same pink. The col's own line comes
    // from makeMap(), which needs no data beyond the feature and so works on the detail map
    // too; this needs the whole collection and therefore lives here.
    const confluenceGroupSource = new ol.source.Vector();
    // Point marks belong above the summits, the lines below them: the same split as the
    // summit highlight, and for the same reason.
    const confluencePointSource = new ol.source.Vector();
    let groupShownFor = null;


    function markLike(feature, type, extra = {}) {
        return new ol.Feature({
            geometry: new ol.geom.Point(ol.proj.fromLonLat(feature.geometry.coordinates)),
            type: type,
            ...extra,
        });
    }

    function riverCourse(river) {
        return new ol.Feature({
            geometry: new ol.geom.LineString(
                river.geometry.coordinates.map(c => ol.proj.fromLonLat(c))),
            type: 'confluence_river',
        });
    }

    function showConfluenceGroup(feature) {
        const type = feature && feature.get('type');
        // Keyed by type as well as pk: a river and a col can share an id, and the two are
        // different highlights.
        const key = type === 'col' || type === 'river' ? `${type}:${feature.get('pk')}` : null;
        if (key === groupShownFor) return;     // pointermove fires far more often than this
        groupShownFor = key;
        confluenceGroupSource.clear();
        confluencePointSource.clear();
        if (key === null) return;

        if (type === 'river') {
            // The course under the cursor, lifted out of the tangle it crosses.
            const river = riversByPk[feature.get('pk')];
            if (river) confluenceGroupSource.addFeature(riverCourse(river));

            // And the high point of the watershed it drains, grown the way a col's two peaks
            // are — the river's course says where the water goes, this says what it comes
            // off. No line to it: the high point tops the whole basin and can sit a long way
            // from the channel, so a line would assert a connection the data does not claim.
            // `.summit`, never `.pk`: the skeleton is keyed by Summit pk, and a high point
            // that is no summit simply has no marker to grow. Using `.pk` here would find an
            // unrelated summit that happens to share the number.
            const watershed = feature.get('watershed_high_point');
            const peak = watershed && watershed.summit && summitsByPk[watershed.summit];
            if (peak) confluencePointSource.addFeature(
                markLike(peak, 'highlight_summit', { prom: peak.properties.prom }));
            return;
        }

        // The two peaks the col sits between — the summit whose key col it is and the higher
        // ground that summit hangs off, the pair the popup names — grown, like a summit
        // under the cursor. Both come from the summit skeleton, so they are known whether or
        // not the col's own detail has arrived yet.
        const minor = summitByKeyCol[feature.get('pk')];
        const major = minor && summitsByPk[minor.properties.prominence_parent];
        [minor, major].forEach(peak => {
            if (peak) confluencePointSource.addFeature(
                markLike(peak, 'highlight_summit', { prom: peak.properties.prom }));
        });

        const confluence = feature.get('confluence');
        if (!confluence || confluence.lon == null) return;
        const mouth = ol.proj.fromLonLat([confluence.lon, confluence.lat]);

        (colsByRiver[confluence.river] || []).forEach(col => {
            if (col.properties.pk === feature.get('pk')) return;   // makeMap draws that one
            const at = ol.proj.fromLonLat(col.geometry.coordinates);
            confluenceGroupSource.addFeature(confluenceLine(at, mouth, 'confluence_sister'));
            // Ringed, to say it shares the confluence the line runs to.
            confluencePointSource.addFeature(markLike(col, 'confluence_col'));
        });

        // The river the col drains into and the one that receives it: the two channels that
        // meet at this confluence. Either may be missing its course, rivers with fewer than
        // two waypoints being left out of the rivers endpoint altogether.
        const river = riversByPk[confluence.river];
        const parent = river && river.properties.parent
            ? riversByPk[river.properties.parent.id]
            : null;
        [river, parent].forEach(r => {
            if (r) confluenceGroupSource.addFeature(riverCourse(r));
        });
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
            keyColFeatureByPk = {};
            keyColLayer.getSource().getFeatures().forEach(f => {
                keyColFeatureByPk[f.get('pk')] = f;
            });
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
        // Optional, so an older template that calls this with five arguments still works.
        rangesUrl ? fetch(rangesUrl).then(r => r.json())
                  : Promise.resolve({type: 'FeatureCollection', features: []}),
    ]).then(([summits, rivers, cols, ranges]) => {
        summitsData = summits;
        colsData = cols;

        summitsByPk = {};
        summits.features.forEach(f => { summitsByPk[f.properties.pk] = f; });
        summitByKeyCol = {};
        summits.features.forEach(f => {
            if (f.properties.kc != null) summitByKeyCol[f.properties.kc] = f;
        });
        colsByPk = {};
        colsByRiver = {};
        cols.features.forEach(f => {
            colsByPk[f.properties.pk] = f;
            const river = f.properties.confluence && f.properties.confluence.river;
            if (river != null) (colsByRiver[river] = colsByRiver[river] || []).push(f);
        });
        riversByPk = {};
        rivers.features.forEach(f => { riversByPk[f.properties.pk] = f; });

        const { map: m, tileLayer, vectorLayer, refreshPopup: refresh } =
            makeMap(summits, styleFor, [19.7, 48.7], 8, onHover);
        map = m;
        refreshPopup = refresh;
        summitLayer = vectorLayer;
        summitLayer.set('name', 'summits');
        summitLayer.getSource().getFeatures().forEach(f => {
            summitFeatureByPk[f.get('pk')] = f;
        });
        // Before the first viewport detail request below, so that it asks for the peaks
        // actually on screen. Where the reader left off wins over the home extent — that is
        // the whole point of remembering it.
        //
        // The controls are restored without notifying, because the layers below are built
        // from them directly: setting them here is enough, and dispatching events at a map
        // whose layers do not exist yet would not be.
        const resumed = parseMapState(window.location.hash);
        applyMapControls(resumed, false);
        if (resumed.position) {
            applyMapPosition(resumed.position);
        } else {
            map.getView().fit(ol.proj.transformExtent(HOME_EXTENT, 'EPSG:4326', 'EPSG:3857'),
                              { padding: [20, 20, 20, 20] });
        }
        renderProminenceLegend();

        // Both names start with "highlight" — that prefix is what featureAt() filters on, so
        // neither layer can ever be hit-tested and steal the hover from the peak underneath.
        [['highlight-lines', highlightLineSource, Z_HIGHLIGHT_LINE],
         ['highlight-marks', highlightPointSource, Z_HIGHLIGHT_MARK]].forEach(([name, source, z]) => {
            const layer = new ol.layer.Vector({ source: source, style: styleFor, zIndex: z });
            layer.set('name', name);
            map.addLayer(layer);
        });

        const confluenceGroupLayer = new ol.layer.Vector({
            source: confluenceGroupSource,
            style: confluenceGroupStyle,
            zIndex: Z_HIGHLIGHT_LINE,
        });
        confluenceGroupLayer.set('name', 'highlight-confluence-group');
        map.addLayer(confluenceGroupLayer);

        const confluencePointLayer = new ol.layer.Vector({
            source: confluencePointSource,
            style: styleFor,
            zIndex: Z_HIGHLIGHT_MARK,
        });
        confluencePointLayer.set('name', 'highlight-confluence-marks');
        map.addLayer(confluencePointLayer);

        const opacitySlider = document.getElementById('map-opacity');
        if (opacitySlider) {
            opacitySlider.addEventListener('input', function() {
                tileLayer.setOpacity(this.value / 100);
            });
        }

        // Added before the rivers so it sits at the bottom of the stack even if a browser
        // ever disagreed about equal zIndexes; off by default, because the boundaries are
        // context and the map is about peaks.
        const rangesLayer = buildRangesLayer(ranges);
        const rangesToggle = document.getElementById('toggle-ranges');
        rangesLayer.setVisible(Boolean(rangesToggle && rangesToggle.checked));
        map.addLayer(rangesLayer);

        if (rangesToggle) {
            rangesToggle.addEventListener('change', function() {
                rangesLayer.setVisible(this.checked);
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
        // Nothing else applies the slider on load: the tile layer's opacity is only ever set
        // by the `input` listener, which has not fired yet.
        tileLayer.setOpacity(resumed.opacity / 100);

        map.on('moveend', scheduleViewportDetail);
        map.on('moveend', rememberMapState);
        requestViewportDetail();       // the peaks the reader starts out looking at

        document.querySelectorAll('input[name="tree"]').forEach(r =>
            r.addEventListener('change', rebuildLineage)
        );
        if (routeToggle) routeToggle.addEventListener('change', rebuildLineage);

        // Every control writes the fragment. One listener per control rather than one
        // delegated to the container: the opacity slider reports `input` and the rest
        // `change`, and a slider that only recorded on release would lose the setting of
        // anyone who drags and then reloads.
        document.querySelectorAll('input[name="tree"]').forEach(r =>
            r.addEventListener('change', rememberMapState)
        );
        for (const id of Object.values(CONTROL_IDS)) {
            const box = document.getElementById(id);
            if (box) box.addEventListener('change', rememberMapState);
        }
        const opacityControl = document.getElementById('map-opacity');
        if (opacityControl) opacityControl.addEventListener('input', rememberMapState);

        // Somebody pasted a link into the address bar of a tab that is already open, or used
        // the back button onto a fragment we did not write. `replaceState` fires no
        // `hashchange`, so this can never be answering itself.
        window.addEventListener('hashchange', function() {
            const wanted = parseMapState(window.location.hash);
            applyMapControls(wanted, true);
            applyMapPosition(wanted.position);
        });
    });
}
