"""
Which summits a map shows at which zoom: the first zoom level from which each one is drawn.

At every zoom level the summits are taken from the most prominent down (the higher first
among equals), and each is shown unless a more prominent one, shown at that level, stands
nearer to it on screen than `spacing` pixels. Only a more prominent summit ever hides one:
a lesser summit shown far out never stands in a greater one's way further in. A summit's
zoom is then the level from which it is shown at every closer level, so nothing blinks out
while zooming in.

So a prominent summit always wins over a less prominent one near it — mostly its own child —
while a modest hill out in the lowland, with nothing more prominent anywhere near, shows from
far out: nothing on screen competes with it. Isolation counts without being measured.

Pure: positions in, zoom levels out. Which line runs to which ancestor at a given level is
the browser's business (`displayParents` in static/js/map.js).
"""
import math

TILE = 256          # pixels across the world at zoom 0, as the web map tiles count them
SPACING = 16        # pixels between two summits' markers, at the least
MAX_ZOOM = 18


def mercator(lon, lat):
    """Web Mercator in pixels at zoom 0: x eastwards, y southwards, the world 256 across."""
    lat = max(min(lat, 85.05), -85.05)
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2
    return (lon + 180) / 360 * TILE, y * TILE


def first_zooms(summits, spacing=SPACING, max_zoom=MAX_ZOOM):
    """
    `summits`: (key, lon, lat, prominence, altitude), prominence or altitude None if unknown.
    Returns {key: zoom}. Two summits on the very same spot never come apart; the lesser one
    gets `max_zoom`, the closest the map goes.
    """
    rows = [(key, *mercator(lon, lat),
             -math.inf if prominence is None else prominence,
             -math.inf if altitude is None else altitude)
            for key, lon, lat, prominence, altitude in summits]
    # Most prominent first, then the higher, then by key so that the order is the same every time.
    rows.sort(key=lambda r: (-r[3], -r[4], str(r[0])))
    # From the closest level outwards: a summit's zoom is the last level, going out, at which
    # it is still shown, with every level inside it shown too.
    zoom = {key: max_zoom for key, *_ in rows}
    hidden_below = set()        # hidden at some level already passed, so at every level out
    for z in range(max_zoom, -1, -1):
        size = spacing / 2 ** z         # one spacing on screen, in zoom-0 pixels
        grid = {}
        for key, x, y, _, _ in rows:
            cx, cy = int(x // size), int(y // size)
            if key in hidden_below:
                continue                # not drawn here, so it hides nothing here either
            # Everything in the grid at this level is drawn and more prominent: only those hide.
            if any(math.hypot(x - ox, y - oy) < size
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for ox, oy in grid.get((cx + dx, cy + dy), ())):
                hidden_below.add(key)
            else:
                grid.setdefault((cx, cy), []).append((x, y))
                zoom[key] = z
    return zoom
