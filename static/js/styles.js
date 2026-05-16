
const SEGMENTS = 20;

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
        stroke: new ol.style.Stroke({ color, width, lineDash: dash })
    });
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

function summitMarker(color, radius=6) {
    return new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 3,
            radius,
            fill: new ol.style.Fill({ color }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 1 }),
        })
    });
}

function colMarker(colour) {
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

function isolationLimitPointStyle(colour, radius=6) {
    return new ol.style.Style({
        image: new ol.style.RegularShape({
            points: 4,
            radius,
            fill: new ol.style.Fill({ colour }),
            stroke: new ol.style.Stroke({ color: '#fff', width: 0.5 }),
        })
    });
}

const isolationCircleStyle = new ol.style.Style({
    stroke: new ol.style.Stroke(
        {
            color: 'rgba(219,40,12,0.6)',
            width: 1.5,
            lineDash: [6, 4],
        }
    ),
    fill:   new ol.style.Fill({ color: 'rgba(219,40,12,0.16)' }),
});


function styleFor(feature) {
    switch (feature.get('type')) {
        case 'summit':                  return summitMarker('#c0392b', 10);
        case 'prominence_parent':       return summitMarker('#2980b9');
        case 'col':                     return colMarker();
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
        default: return [];
    }
}
