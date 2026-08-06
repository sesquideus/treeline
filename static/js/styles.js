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
// are the ones that carry meaning in the domain: 1500 m is the ultra definition, 600 m and
// 200 m the conventional steps below it, then 100 m and 30 m — the latter being the usual
// cutoff for counting as an independent summit at all.
// A multi-hue ramp in the style of inferno/magma: gold → amber → orange → red → magenta →
// violet, monotone in lightness (0.820 → 0.241) with 160° of hue travel on top of it. The
// one-hue-per-ramp rule is deliberately broken: six steps of a single hue put adjacent bands
// only ΔE 6.4 apart, close enough that the bottom half of the legend read as one colour.
// Steps are spaced *unevenly on purpose* — ΔL grows from 0.073 at the top to 0.145 at the
// bottom, and hue likewise. Marker area shrinks with prominence, so the low bands have the
// least ink to carry their colour and need the most separation to stay distinguishable: the
// bottom pairs sit at ΔE 17–19, the top ones at 9–13, where the marks are large enough to
// read a smaller difference. Worst pair under simulated protanopia/deuteranopia is 7.2.
// Ramp direction: light for the big peaks, dark for the small ones. It runs against the
// usual "darker means more" convention, and it works here only because size carries the
// magnitude too — an ultra is a large, pale, ink-outlined mark, a subsidiary bump a small
// dark dot. Reverse the colour column to put it back the conventional way round.
// The cost of a vivid top end: the gold clears only 1.7:1 against the surface and 1.2:1
// against forest-green tiles, under the 2:1 an ordinal ramp's lightest step is supposed to
// hold, so it leans on SUMMIT_OUTLINE for its edge. Darkening it toward #c5af00 buys that
// contrast back and costs the vibrancy.
const PROMINENCE_BANDS = [
    { min: 1500, key: 'ultra',      label: 'ultra (≥ 1500 m)',     colour: '#d6c900' },
    { min: 600,  key: 'major',      label: 'major (600–1500 m)',   colour: '#d9a400' },
    { min: 200,  key: 'notable',    label: 'notable (200–600 m)',  colour: '#d36f00' },
    { min: 100,  key: 'minor',      label: 'minor (100–200 m)',    colour: '#c60129' },
    { min: 30,   key: 'small',      label: 'small (30–100 m)',     colour: '#7a0057' },
    { min: 0,    key: 'subsidiary', label: 'subsidiary (< 30 m)',  colour: '#2e004d' },
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
    element.innerHTML = '<span class="legend-title">Prominence</span>'
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

const GREEN  = [39, 174, 96];
const ISOLATION_BEGIN = [241, 86, 255];
const ISOLATION_MID = [241, 86, 255];
const ISOLATION_END = [241, 86, 255];
const RIVER  = [11, 34, 255];
const RED    = [192, 57, 43];
const BLUE   = [43, 57, 192];
const PURPLE = [142, 68, 173];


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
            width: 2,
        }),
        zIndex: Z_LINE,
    }));
}

function gradientLine(feature, c1, c2) {
    return segmentStyles(denseCoords(feature.getGeometry().getCoordinates()), c1, c2);
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
    return new ol.style.Style({
        image: new ol.style.Circle({
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 1.5 }),
        }),
        zIndex: Z_POINT,
    });
}

// Summit markers are outlined in ink rather than the usual white surface ring: the fill now
// gets *lighter* as prominence rises, and a defined edge is what keeps the big pale ultras
// legible against the terrain. Small dark markers lose nothing by it.
const SUMMIT_OUTLINE = 'rgba(11, 11, 11, 0.8)';

function summitMarker(color, radius = 6, { outline = SUMMIT_OUTLINE, zIndex = Z_POINT } = {}) {
    return new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 3,
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: outline, width: 1 }),
        }),
        zIndex,
    });
}

function colMarker(colour, size) {
    return new ol.style.Style({
        image: new ol.style.Circle({
            radius: 5,
            fill: new ol.style.Fill({ color: '#0040FF' }),
            stroke: new ol.style.Stroke({ color: '#FFFFFF', width: 1.5 }),
        }),
        zIndex: Z_POINT,
    });

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


// Hover highlight: a ring drawn around the key col and the parent of whichever peak is under
// the cursor. Cyan is deliberately outside the prominence ramp's warm gold-to-violet range, so
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

function highlightParent(prom) {
    const eased = easeOutBack(highlightProgress);
    const radius = prominenceRadius(prom) * (1 + (HIGHLIGHT_GROW - 1) * eased);
    return [
        // An oversized translucent triangle fading in behind the marker: the "shine".
        new ol.style.Style({
            image: new ol.style.RegularShape({
                points: 3,
                radius: radius * 1.5,
                fill: new ol.style.Fill({ color: `rgba(0, 182, 224, ${0.32 * eased})` }),
            }),
            zIndex: Z_POINT,
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
            zIndex: Z_POINT + 1,
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

function highlightRing(radius) {
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
                stroke: new ol.style.Stroke({ color: HIGHLIGHT_COLOUR, width: 2.5 }),
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
                                                            { zIndex: prominenceZIndex(prom) });
        case 'prominence_parent':       return summitMarker('#2980b9');
        case 'col':                     return colMarker();
        // The col keeps a ring — it is a fixed 5px circle, so a ring at 10 clears it — while the
        // parent is redrawn as its own triangle, grown and lit, rather than annotated.
        case 'highlight_col':           return highlightRing(10);
        case 'highlight_parent':        return highlightParent(prom);
        case 'highlight_line':          return highlightLine();
        case 'isolation_point':         return dot('#f1c40f');
        case 'isolation_parent':        return dot('#8e44ad');
        case 'encirclement_parent':     return summitMarker('#c0392b', 12);
        case 'isolation_circle':        return isolationCircleStyle;
        case 'isolation_line_first':    return gradientLine(feature, ISOLATION_BEGIN, ISOLATION_MID);
        case 'isolation_line_second':   return gradientLine(feature, ISOLATION_MID, ISOLATION_END);
        case 'prominence_line_first':   return gradientLine(feature, BLUE, RED);
        case 'prominence_line_second':  return gradientLine(feature, RED, YELLOW);
        case 'encirclement_line':       return [dashedLine('rgba(35,14,4,0.8)')];
        case 'slope_line':              return gradientLine(feature, RED, YELLOW);
        case 'horizon_king':            return [
            summitMarker(prominenceBand(prom).colour, prominenceRadius(prom),
                         { zIndex: prominenceZIndex(prom) }),
            new ol.style.Style({
                text: new ol.style.Text({
                    text: '👑',
                    font: '14px sans-serif',
                    offsetY: -10,
                }),
                zIndex: prominenceZIndex(prom),
            }),
        ];
        default: return [];
    }
}
