"""
The global map's summits and cols, one view at a time.

    ?bbox=minLon,minLat,maxLon,maxLat&zoom=Z&mode=prominence|isolation|slope|horizon|none
    ?river=PK                                    a river's cols, for a confluence hovered

What comes back for a view is what the map draws there at zoom Z in that mode:

- every summit in the view that shows at Z;
- every summit that shows at Z whose edge to its parent crosses the view, even when both ends
  lie outside it: the line runs across the screen, so both its ends must be there to draw it;
- the parents of all of those, wherever they stand (a parent shows at least as early as its
  child, so at Z too);
- the key cols of every summit returned, and the cols nobody's key col is that lie in the view.

Which summit shows from which zoom, and the edges as lines, are stored by
`mountains.map_zooms`; a request only reads them, rebuilding first if the data has changed.
Features are the same skeletons as `summits/geo.json` and `cols/geo.json`, with each summit's
zoom in every mode (`zooms`) added: the map copies the one for its mode into `vz`, so that a
change of mode needs nothing fetched again for what is already loaded.
"""
from django.contrib.gis.geos import Polygon
from django.core.exceptions import SuspiciousOperation
from django.db.models import Max
from django.http import JsonResponse
from django.views import View

from core.functions.declutter import MAX_ZOOM
from mountains.map_zooms import ensure_map_zooms
from mountains.models import Col, Summit, SummitMapZoom
from mountains.models.col import COL_SKELETON_FIELDS, col_skeleton_feature
from mountains.models.summit import MAP_MODES
from mountains.models.summit.model import SUMMIT_SKELETON_FIELDS, summit_skeleton_feature
from mountains.views.cache import cached_json
from mountains.views.viewport import in_bbox, parse_bbox

MODES = (*MAP_MODES, 'none')
ZOOM_FIELDS = ('map_zoom__z', *(f'map_zoom__vz_{mode}' for mode in MAP_MODES))


def parse_zoom(raw):
    try:
        zoom = int(raw)
    except (TypeError, ValueError):
        raise SuspiciousOperation('zoom must be an integer')
    return max(0, min(zoom, MAX_ZOOM))


def parse_mode(raw):
    if raw not in MODES:
        raise SuspiciousOperation(f'mode must be one of {", ".join(MODES)}')
    return raw


def clamp_lat(box, limit=85.0511):
    """ `box` with its latitudes within Web Mercator's (which has no ±90°). """
    from django.contrib.gis.geos import MultiPolygon, Polygon
    polys = list(box) if box.geom_type == 'MultiPolygon' else [box]
    out = []
    for p in polys:
        x0, y0, x1, y1 = p.extent
        out.append(Polygon.from_bbox((x0, max(y0, -limit), x1, min(y1, limit))))
    clamped = out[0] if len(out) == 1 else MultiPolygon(*out)
    clamped.srid = box.srid
    return clamped


def highest_altitude():
    """The altitude of the highest summit there is: its marker says nothing is higher."""
    return cached_json('mountains:highest-summit',
                       lambda: Summit.objects.aggregate(h=Max('point__altitude'))['h'])


def summit_collection(pks):
    highest = highest_altitude()
    features = []
    for row in Summit.objects.filter(pk__in=pks, point__location__isnull=False).values_list(
            *SUMMIT_SKELETON_FIELDS, *ZOOM_FIELDS):
        feature = summit_skeleton_feature(row[:len(SUMMIT_SKELETON_FIELDS)], highest)
        z, *vz = row[len(SUMMIT_SKELETON_FIELDS):]
        feature['properties']['z'] = z
        feature['properties']['zooms'] = {'none': z, **dict(zip(MAP_MODES, vz))}
        features.append(feature)
    return {'type': 'FeatureCollection', 'features': features}


def col_collection(queryset):
    return {'type': 'FeatureCollection',
            'features': [col_skeleton_feature(row) for row in queryset.values_list(*COL_SKELETON_FIELDS)
                         if row[1] is not None]}


class MapFeaturesView(View):
    def get(self, request):
        if (river := request.GET.get('river')) is not None:
            try:
                river = int(river)
            except ValueError:
                raise SuspiciousOperation('river must be an integer')
            return JsonResponse({'summits': summit_collection([]),
                                 'cols': col_collection(Col.objects.filter(confluence_river_id=river))})

        bbox = parse_bbox(request.GET.get('bbox'))
        if bbox is None:
            raise SuspiciousOperation('bbox or river is required')
        bbox.srid = 4326
        zoom = parse_zoom(request.GET.get('zoom'))
        mode = parse_mode(request.GET.get('mode', 'prominence'))
        ensure_map_zooms()

        shown = {'map_zoom__z__lte' if mode == 'none' else f'map_zoom__vz_{mode}__lte': zoom}
        pks = set(in_bbox(Summit.objects.filter(**shown), 'point__location', bbox).values_list('pk', flat=True))
        if mode != 'none':
            # Edges crossing the view, asked in the projection they are drawn in.
            mercator = clamp_lat(bbox).transform(3857, clone=True)
            pks |= set(SummitMapZoom.objects.filter(**{f'vz_{mode}__lte': zoom,
                                                       f'edge_{mode}__intersects': mercator})
                       .values_list('summit_id', flat=True))
            # The other ends: a parent shows at least as early as its child.
            pks |= set(Summit.objects.filter(pk__in=pks, **{f'{MAP_MODES[mode]}__isnull': False})
                       .values_list(MAP_MODES[mode], flat=True))

        summits = summit_collection(pks)
        key_cols = {f['properties']['kc'] for f in summits['features'] if f['properties']['kc']}
        # The cols that are nobody's key col, asked apart: OR-ed with the key cols into one
        # query, the bbox could not use the spatial index (0.1 s per tile, now a millisecond)
        free = set(in_bbox(Col.objects.filter(key_for__isnull=True), 'point__location', bbox)
                   .values_list('pk', flat=True))
        cols = col_collection(Col.objects.filter(pk__in=key_cols | free))
        return JsonResponse({'summits': summits, 'cols': cols})
