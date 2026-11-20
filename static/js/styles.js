const SEGMENTS = 20;

// Draw order *within* a single vector layer. Points sit above lines so that a marker
// crossed by a lineage line stays visible and stays the topmost hit target on click.
const Z_AREA  = 10;
const Z_LINE  = 20;
const Z_POINT = 40;

// Summit markers scale with prominence: a bump with no prominence to speak of renders at
// SUMMIT_MIN_SCALE of the base radius, Everest at SUMMIT_MAX_SCALE. The exponent shapes the
// curve between them. The distribution is extremely tail-heavy — median prominence is under
// 200 m against Everest's 8849 — so a cube root, which keeps the crowded bottom half
// legible without flattening the top the way a log curve does.
const SUMMIT_BASE_RADIUS = 10;
const SUMMIT_MIN_SCALE = 0.6;
const SUMMIT_MAX_SCALE = 1.75;
const SUMMIT_SCALE_EXPONENT = 1 / 3;
const MAX_PROMINENCE = 8848.86;   // Mount Everest

// Size is a continuous encoding and cannot show a threshold; colour does that. The cutoffs
// are the ones that carry meaning in the domain: 1500 m is the ultra definition, 600 m,
// 300 m and 200 m the conventional steps below it, then 100 m and 30 m — the latter being
// the usual cutoff for counting as an independent summit at all.
// A multi-hue ramp in the style of inferno/magma: yellow → amber → orange → red → magenta →
// purple → indigo, monotone in lightness (0.819 → 0.226) with 200° of hue travel on top of
// it. The one-hue-per-ramp rule is deliberately broken: seven steps of a single hue put
// adjacent bands barely ΔE 5 apart, close enough that the legend would read as one colour.
// Steps are spaced *unevenly on purpose* — ΔL grows from 0.090 at the top to 0.127 at the
// bottom, and hue likewise. Marker area shrinks with prominence, so the low bands have the
// least ink to carry their colour and need the most separation to stay distinguishable: the
// bottom pairs sit at ΔE 15–17, the top ones at 12–14, where the marks are large enough to
// read a smaller difference. Hue travel is what pays for the seventh band: 200° across seven
// steps against 160° across six, which keeps every adjacent pair at ΔE 12.4 or better where
// the six-band ramp bottomed out at 9.4 and squeezing a seventh step into it dropped that to
// 9.3. Lightness range is unchanged — extending it would have cost the top band the little
// contrast it has. Worst adjacent pair under simulated deuteranopia is 9.0 (ultra against
// major, which collapse onto lightness alone); under protanopia 8.9 (small against
// subsidiary). Both were ~7 with a seventh band on the old ramp.
// What the extra hue travel costs: the dark end lands on indigo rather than violet, so
// subsidiary summits sit near the blue of cols and rivers and are told apart by lightness
// and marker shape rather than hue. Pulling the travel back to ~185° returns a violet end
// and gives up about ΔE 1 per pair.
// Ramp direction: light for the big peaks, dark for the small ones. It runs against the
// usual "darker means more" convention, and it works here only because size carries the
// magnitude too — an ultra is a large, pale, ink-outlined mark, a subsidiary bump a small
// dark dot. Reverse the colour column to put it back the conventional way round.
// The cost of a vivid top end: the yellow clears only 1.7:1 against the surface and 1.2:1
// against forest-green tiles, under the 2:1 an ordinal ramp's lightest step is supposed to
// hold, so it leans on SUMMIT_OUTLINE for its edge. Darkening it toward #bfbb00 buys that
// contrast back (2.0:1) and costs the vibrancy.
const PROMINENCE_BANDS = [
    { min: 1500, key: 'ultra',      label: 'ultra (≥ 1500 m)',     colour: '#cfcb26' },
    { min: 600,  key: 'major',      label: 'major (600–1500 m)',   colour: '#d99a1f' },
    { min: 300,  key: 'notable',    label: 'notable (300–600 m)',  colour: '#da6217' },
    { min: 200,  key: 'modest',     label: 'modest (200–300 m)',   colour: '#d01841' },
    { min: 100,  key: 'minor',      label: 'minor (100–200 m)',    colour: '#9a0f6b' },
    { min: 30,   key: 'small',      label: 'small (30–100 m)',     colour: '#57087a' },
    { min: 0,    key: 'subsidiary', label: 'subsidiary (< 30 m)',  colour: '#07035e' },
];
const PROMINENCE_UNKNOWN = { key: 'unknown', label: 'unknown', colour: '#898781' };

function prominenceBand(prominence) {
    // `null >= 0` is true in JS, so null has to be excluded before the comparison.
    if (prominence == null || !(prominence >= 0)) return PROMINENCE_UNKNOWN;
    return PROMINENCE_BANDS.find(band => prominence >= band.min) || PROMINENCE_UNKNOWN;
}

// Colour must not be the only channel carrying the bands, so the legend is generated from
// the same array the styles read. Silently does nothing on pages with no legend element.
function renderProminenceLegend(element = document.getElementById('prominence-legend')) {
    if (!element) return;
    const swatch = band => `
        <span class="legend-row">
            <span class="legend-swatch" style="background:${band.colour}"></span>
            ${band.label}
        </span>`;
    element.innerHTML = '<span class="group-title">Prominence</span>'
        + PROMINENCE_BANDS.map(swatch).join('')
        + swatch(PROMINENCE_UNKNOWN);
}

function prominenceRadius(prominence, base = SUMMIT_BASE_RADIUS) {
    if (prominence == null || !(prominence > 0)) return base * SUMMIT_MIN_SCALE;
    const t = Math.min(prominence / MAX_PROMINENCE, 1);
    const scale = SUMMIT_MIN_SCALE
        + (SUMMIT_MAX_SCALE - SUMMIT_MIN_SCALE) * Math.pow(t, SUMMIT_SCALE_EXPONENT);
    return base * scale;
}

// Where summits overlap, the more prominent one takes the top of the stack — and with it
// the click, since forEachFeatureAtPixel hands back the topmost feature first. The span is
// wide so that neighbouring peaks rarely land on the same integer and fall back to source
// order; the curve matches prominenceRadius() so the stacking follows the visible sizes.
const Z_SUMMIT_SPAN = 1000;

function prominenceZIndex(prominence) {
    if (prominence == null || !(prominence > 0)) return Z_POINT;
    const t = Math.min(prominence / MAX_PROMINENCE, 1);
    return Z_POINT + Math.round(Math.pow(t, SUMMIT_SCALE_EXPONENT) * Z_SUMMIT_SPAN);
}

// The lineage lines, as the global map draws them (`lineageStyle()` in map.js) and the
// detail maps after it, so a climb or an isolation leg reads the same on either. Each is a
// gradient from the peak towards its parent, split at the waypoint when the line is routed.
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

// The line parent: from the summit, down through its key col, up to the first higher one.
const LINE_COLOUR_A              = [220, 110,   0, 1];   // orange
const LINE_COLOUR_B              = [ 90, 170,   0, 1];   // green


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

// Styles are built once and reused. A style function runs for every feature in view on every
// frame, and a marker built afresh each time paints a canvas of its own each time: with ten
// thousand peaks on screen that was most of the time a pan took. Markers are cached by what
// makes them differ; a gradient line, which carries its own geometry, per feature.
const styleCache = new Map();

function cachedStyle(key, build) {
    let style = styleCache.get(key);
    if (style === undefined) {
        style = build();
        styleCache.set(key, style);
    }
    return style;
}

// Per feature, and dropped with it. Keyed by the colours too: the same line is drawn in
// another gradient when the hierarchy on show changes. A feature's geometry is never moved
// on these maps, so the segments cut from it stay right.
const lineStyleCache = new WeakMap();

function cachedLineStyles(feature, key, build) {
    const held = lineStyleCache.get(feature);
    if (held && held.key === key) return held.styles;
    const styles = build();
    lineStyleCache.set(feature, { key, styles });
    return styles;
}

function segmentStyles(coords, c1, c2) {
    return coords.slice(0, -1).map((_, i) => new ol.style.Style({
        geometry: new ol.geom.LineString([coords[i], coords[i+1]]),
        stroke: new ol.style.Stroke({
            color: interpolateColor(c1, c2, i / (coords.length - 2)),
            width: 2,
        }),
        zIndex: Z_LINE,
    }));
}

function gradientLine(feature, c1, c2) {
    return cachedLineStyles(feature, `${c1}|${c2}`, () =>
        segmentStyles(denseCoords(feature.getGeometry().getCoordinates()), c1, c2));
}

// A climb to the parent as one feature, the way the detail and comparison maps serve it:
// [peak, col, parent] while routed through the col, [peak, parent] once straightened. The
// global map draws the same thing as two features, `peak_to_col` and `col_to_parent`, and
// these are its gradients — so the routed line is those two, and the straight one runs
// from the first colour of the one to the last of the other, as the global map's does.
function prominenceClimb(feature) {
    return cachedLineStyles(feature, 'climb', () => prominenceClimbStyles(feature));
}

function prominenceClimbStyles(feature) {
    const coords = feature.getGeometry().getCoordinates();
    if (coords.length < 3) {
        return segmentStyles(denseCoords(coords),
                             PROMINENCE_PEAK_TO_COL_A, PROMINENCE_COL_TO_PARENT_B);
    }
    const col = coords.length - 2;
    return [
        ...segmentStyles(denseCoords(coords.slice(0, col + 1)),
                         PROMINENCE_PEAK_TO_COL_A, PROMINENCE_PEAK_TO_COL_B),
        ...segmentStyles(denseCoords(coords.slice(col)),
                         PROMINENCE_COL_TO_PARENT_A, PROMINENCE_COL_TO_PARENT_B),
    ];
}

function interpolateColor(c1, c2, t) {
    const r = Math.round(c1[0] + (c2[0] - c1[0]) * t);
    const g = Math.round(c1[1] + (c2[1] - c1[1]) * t);
    const b = Math.round(c1[2] + (c2[2] - c1[2]) * t);
    return `rgb(${r}, ${g}, ${b})`;
}

function withAlpha(color, alpha) {
    return `rgba(${color[0]},${color[1]},${color[2]},${alpha})`;
}

function dashedLine(color, width=3, dash=[8, 5]) {
    return new ol.style.Style({
        stroke: new ol.style.Stroke({ color, width, lineDash: dash }),
        zIndex: Z_LINE,
    });
}

function dot(color, radius=6) {
    return cachedStyle(`dot|${color}|${radius}`, () => new ol.style.Style({
        image: new ol.style.Circle({
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 1.5 }),
        }),
        zIndex: Z_POINT,
    }));
}

// Summit markers are outlined in ink rather than the usual white surface ring: the fill now
// gets *lighter* as prominence rises, and a defined edge is what keeps the big pale ultras
// legible against the terrain. Small dark markers lose nothing by it.
const SUMMIT_OUTLINE = 'rgba(11, 11, 11, 0.8)';

function summitMarker(color, radius = 6, { outline = SUMMIT_OUTLINE, zIndex = Z_POINT } = {}) {
    return cachedStyle(`summit|${color}|${radius}|${outline}|${zIndex}`, () => new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 3,
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: outline, width: 1 }),
        }),
        zIndex,
    }));
}

// A glyph riding above a marker, saying what kind of point it is where colour alone cannot.
function glyphAbove(glyph, offsetY = -10, zIndex = Z_POINT) {
    return cachedStyle(`glyph|${glyph}|${offsetY}|${zIndex}`, () => new ol.style.Style({
        text: new ol.style.Text({
            text: glyph,
            font: '14px sans-serif',
            offsetY: offsetY,
        }),
        zIndex: zIndex,
    }));
}

// A summit as the global map draws it: its prominence band's colour, its size, and its
// place in the stack. Every peak on a detail map is one of these, whatever its relation to
// the page's own; the lines say what that relation is.
function bandSummit(prom) {
    return summitMarker(prominenceBand(prom).colour, prominenceRadius(prom),
                        { zIndex: prominenceZIndex(prom) });
}

// The ordinary marker with a glyph riding above it. Used for the roots of a hierarchy, which
// have no parent to draw a line to and would otherwise look like any other peak.
function markedSummit(prom, glyph, offsetY = -10) {
    return [
        summitMarker(prominenceBand(prom).colour, prominenceRadius(prom),
                     { zIndex: prominenceZIndex(prom) }),
        glyphAbove(glyph, offsetY, prominenceZIndex(prom)),
    ];
}

// Above every summit, the lit parent included: a col is a 5 px dot, and a key col is often a
// few pixels from a peak whose triangle is several times wider — under it, the col could be
// neither hovered nor clicked. It only matters where cols and summits share a layer, as on
// the detail maps; the global map keeps its cols in a layer of their own.
const Z_COL = Z_POINT + Z_SUMMIT_SPAN + 2;
// Above the cols in turn: the summit a detail page is about. Fitted to twenty children, a
// peak's own page can be zoomed out until a neighbouring col shares its very pixel.
const Z_SUBJECT = Z_COL + 1;

function colMarker(colour, size) {
    return cachedStyle(`col|${size || 5}`, () => new ol.style.Style({
        image: new ol.style.Circle({
            radius: size || 5,
            fill: new ol.style.Fill({ color: '#0040FF' }),
            stroke: new ol.style.Stroke({ color: '#FFFFFF', width: 1.5 }),
        }),
        zIndex: Z_COL,
    }));

    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24">
        <circle cx="12" cy="12" r="4"
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

function isolationLimitPointStyle(colour, radius=6) {
    return new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 4,
            radius,
            fill: new ol.style.Fill({ colour }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 0.5 }),
        }),
        zIndex: Z_POINT,
    });
}

// Ranges are backdrop, not subject: a thin outline and a fill faint enough that summit
// markers and lineage lines read clearly through a stack of nested ranges.
const rangeStyle = new ol.style.Style({
    stroke: new ol.style.Stroke({
        color: 'rgba(70, 90, 60, 0.55)',
        width: 1,
    }),
    fill: new ol.style.Fill({
        color: 'rgba(120, 150, 100, 0.10)',
    }),
    zIndex: Z_AREA,
});

// A river on its own page: the global map's blue, thicker, since here it is the subject.
const riverLine = new ol.style.Style({
    stroke: new ol.style.Stroke({ color: '#1a40f9', width: 4 }),
});
// On a col's page, the two rivers meeting below it: the major one as above, the minor one
// that flows into it thinner, so the confluence reads as a tributary joining a river.
const minorRiverLine = new ol.style.Style({
    stroke: new ol.style.Stroke({ color: '#1a40f9', width: 2 }),
});
// On a river's page, the rest of the water's way to the root: the same blue, faded, since
// it is no longer the river the page is about.
const downstreamLine = new ol.style.Style({
    stroke: new ol.style.Stroke({ color: 'rgba(26, 64, 249, 0.35)', width: 3 }),
});

// On a col's page, the way a drop from either side runs down to where the two waters meet:
// the river's blue, haloed, over the rivers it follows.
const drainageRun = [
    new ol.style.Style({
        stroke: new ol.style.Stroke({ color: 'rgba(0, 170, 255, 0.45)', width: 10 }),
        zIndex: 5,
    }),
    new ol.style.Style({
        stroke: new ol.style.Stroke({ color: '#1a40f9', width: 3 }),
        zIndex: 6,
    }),
];

const isolationCircleStyle = new ol.style.Style({
    stroke: new ol.style.Stroke(
        {
            color: 'rgba(219, 40, 12, 0.6)',
            width: 1.5,
            lineDash: [6, 4],
        }
    ),
    fill:   new ol.style.Fill(
        {
            color: 'rgba(219, 40, 12, 0.16)'
        }
    ),
    zIndex: Z_AREA,
});


// Hover highlight: a ring drawn around the parent of whichever peak is under the cursor, and
// around the waypoint its lineage passes through — the key col in prominence mode, the
// nearest higher point in isolation mode. Cyan is deliberately outside the prominence ramp's warm gold-to-violet range, so
// a ring never reads as another band; and it is a ring rather than a fill so the marker it
// annotates stays visible, colour and size intact, inside it. The white halo underneath keeps
// it legible on the forested green parts of the basemap as well as the pale ones.
const HIGHLIGHT_COLOUR = '#00b6e0';
const HIGHLIGHT_HALO = 'rgba(255, 255, 255, 0.9)';

// The parent's entrance animation. OpenLayers has no declarative animation, so the style
// function reads a progress value (0 = just appeared, 1 = settled) that map.js advances with
// requestAnimationFrame plus a map.render() per frame.
const HIGHLIGHT_MS = 220;          // short enough to read as a response to the cursor
const HIGHLIGHT_GROW = 1.7;        // final size of the parent triangle, relative to its own
let highlightProgress = 1;

function setHighlightProgress(value) {
    highlightProgress = Math.min(Math.max(value, 0), 1);
}

// Overshoots just past the target before settling, which is what makes the growth read as a
// snap rather than a slow inflation.
function easeOutBack(t) {
    const s = 1.70158;
    const u = t - 1;
    return 1 + (s + 1) * u * u * u + s * u * u;
}

// Mix a hex colour toward white. Lifting the parent's own band colour is what makes it look
// lit while keeping it identifiably its own band — a flat white marker would lose that.
function lighten(hex, amount) {
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
    const mix = c => Math.round(c + (255 - c) * amount);
    return `rgb(${mix(r)}, ${mix(g)}, ${mix(b)})`;
}

// The summit under the cursor: its own marker, grown. Less than the parent's
// HIGHLIGHT_GROW and without the cyan shine — this one is not being pointed *at*, it is
// simply the thing being read, and it has to stay the same colour and shape to stay
// recognisable as its own prominence band.
const HIGHLIGHT_HOVER_GROW = 1.4;

function highlightSummit(prom) {
    const eased = easeOutBack(highlightProgress);
    return summitMarker(
        prominenceBand(prom).colour,
        prominenceRadius(prom) * (1 + (HIGHLIGHT_HOVER_GROW - 1) * eased),
        { zIndex: prominenceZIndex(prom) + 1 },
    );
}

function highlightParent(prom) {
    return litParent(prom, easeOutBack(highlightProgress));
}

// The parent as the global map lights it on hover, at rest: on a summit's or a col's page
// it is always the one being pointed at. `eased` runs 0 → 1 as the hover animation does.
// The marker sits above every other point by default, because on a detail page it shares
// its position with others — an encirclement parent is often the same peak — and the
// marker on top is the one a click reaches.
function litParent(prom, eased = 1, zIndex = Z_POINT + Z_SUMMIT_SPAN + 1) {
    const radius = prominenceRadius(prom) * (1 + (HIGHLIGHT_GROW - 1) * eased);
    return [
        // An oversized translucent triangle fading in behind the marker: the "shine".
        new ol.style.Style({
            image: new ol.style.RegularShape({
                points: 3,
                radius: radius * 1.5,
                fill: new ol.style.Fill({ color: `rgba(0, 182, 224, ${0.32 * eased})` }),
            }),
            // Under every point, not just under its own marker: the shine is half again as
            // wide as the triangle, and above a col a few pixels away it would take that
            // col's hover and click — hit testing goes by z-index, transparent or not.
            zIndex: Z_POINT - 1,
        }),
        new ol.style.Style({
            image: new ol.style.RegularShape({
                points: 3,
                radius,
                fill: new ol.style.Fill({
                    color: lighten(prominenceBand(prom).colour, 0.4 * eased),
                }),
                stroke: new ol.style.Stroke({ color: HIGHLIGHT_HALO, width: 2 }),
            }),
            zIndex,
        }),
    ];
}

// The peak-to-parent connection, over the top of the lineage line it follows: a white halo
// under a cyan core, so it stays readable wherever it crosses.
function highlightLine() {
    return [
        new ol.style.Style({
            stroke: new ol.style.Stroke({ color: HIGHLIGHT_HALO, width: 7 }),
            zIndex: Z_LINE,
        }),
        new ol.style.Style({
            stroke: new ol.style.Stroke({ color: HIGHLIGHT_COLOUR, width: 3 }),
            zIndex: Z_LINE + 1,
        }),
    ];
}

// A col sharing the hovered col's confluence. Ringed in the confluence pink rather than the
// hover cyan: it belongs to the pink group of lines converging on the river mouth, not to the
// summit-hover highlight, and the two can be on screen at once.
const CONFLUENCE_MARK = 'rgba(230, 60, 230, 0.95)';

function highlightRing(radius, colour = HIGHLIGHT_COLOUR) {
    return [
        new ol.style.Style({
            image: new ol.style.Circle({
                radius: radius,
                stroke: new ol.style.Stroke({ color: HIGHLIGHT_HALO, width: 5 }),
            }),
            zIndex: Z_POINT,
        }),
        new ol.style.Style({
            image: new ol.style.Circle({
                radius: radius,
                stroke: new ol.style.Stroke({ color: colour, width: 2.5 }),
            }),
            zIndex: Z_POINT,
        }),
    ];
}


function styleFor(feature) {
    const prom = feature.get('prom');
    switch (feature.get('type')) {
        case 'summit':                  return summitMarker(prominenceBand(prom).colour,
                                                            prominenceRadius(prom),
                                                            { zIndex: feature.get('subject')
                                                                  ? Z_SUBJECT
                                                                  : prominenceZIndex(prom) });
        case 'prominence_parent':       return summitMarker('#2980b9');
        // The detail maps. Every summit is drawn as the global map draws it, in its band;
        // only the parent is set apart, lit as the global map lights it on hover.
        case 'key_for_summit':          return bandSummit(prom);
        case 'lit_parent':              return litParent(prom);
        case 'prominence_child':        return bandSummit(prom);
        // On a river's page, a summit whose key col has the river as its confluence.
        case 'confluence_summit':       return bandSummit(prom);
        // ...and the way down from it to that key col: the first half of a climb.
        case 'key_col_line':            return gradientLine(feature, PROMINENCE_PEAK_TO_COL_A,
                                                            PROMINENCE_PEAK_TO_COL_B);
        // A child's climb up to the page's summit: the same line as any climb.
        case 'child_line':              return prominenceClimb(feature);
        case 'river':                   return [feature.get('role') === 'minor'
                                                    ? minorRiverLine : riverLine];
        case 'drainage_run':            return drainageRun;
        case 'downstream':              return downstreamLine;
        case 'mouth':                   return dot('#1a40f9', 5);
        case 'river_source':            return dot('#1a40f9');
        case 'source_summit':           return summitMarker('#16a085');
        // A summit as a summit; a named point nobody has catalogued as one, as a point.
        case 'watershed_high_point':    return feature.get('pk') != null
                                                    ? summitMarker('#d35400')
                                                    : dot('#8e44ad');
        // Where two chains meet on the comparison map. The chain's own blue, drawn larger
        // and forked, rather than a prominence-coloured triangle: on that map it is a
        // waypoint on the walk from one peak to the other, not one of the peaks compared.
        case 'common_ancestor':         return [summitMarker('#2980b9', 9),
                                                glyphAbove('\u22d4', -13)];
        // A summit's own key col is drawn larger than its children's, which share its map.
        case 'col':                     return feature.get('key') ? colMarker(null, 8)
                                                                  : colMarker();
        // The col keeps a ring — it is a fixed 5px circle, so a ring at 10 clears it — while the
        // parent is redrawn as its own triangle, grown and lit, rather than annotated.
        case 'highlight_summit':        return highlightSummit(prom);
        case 'highlight_waypoint':      return highlightRing(10);
        case 'confluence_col':          return highlightRing(8, CONFLUENCE_MARK);
        case 'highlight_parent':        return highlightParent(prom);
        case 'highlight_line':          return highlightLine();
        case 'isolation_point':         return dot('#f1c40f');
        case 'isolation_parent':        return bandSummit(prom);
        case 'encirclement_parent':     return bandSummit(prom);
        case 'range':                   return rangeStyle;
        case 'isolation_circle':        return isolationCircleStyle;
        // The global map's `peak_to_nhp` and `nhp_to_parent`.
        case 'isolation_line_first':    return gradientLine(feature, ISOLATION_PEAK_TO_NHP_A,
                                                            ISOLATION_PEAK_TO_NHP_B);
        case 'isolation_line_second':   return gradientLine(feature, ISOLATION_NHP_TO_PARENT_A,
                                                            ISOLATION_NHP_TO_PARENT_B);
        // The whole climb in one line — peak, key col, parent — as the detail and compare
        // maps serve it.
        case 'prominence_line':         return prominenceClimb(feature);
        case 'encirclement_line':       return [dashedLine('rgba(35,14,4,0.8)')];
        // A summit with nothing above it in the hierarchy on show: the crown for a horizon
        // king, a question mark where a slope parent was never worked out.
        case 'horizon_king':            return markedSummit(prom, '👑');
        // Two kinds of missing parent, told apart: nothing higher exists (so there can be
        // no parent) versus nobody has computed one. The question mark sits higher than the
        // crown — the glyph has no flat base and reads as touching the triangle otherwise.
        case 'slope_root':              return markedSummit(prom, '∅', -16);
        case 'slope_unknown':           return markedSummit(prom, '❓', -17);
        case 'horizon_unknown':         return markedSummit(prom, '❓', -17);
        default: return [];
    }
}
