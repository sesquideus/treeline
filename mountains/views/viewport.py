"""
Viewport detail endpoints.

The flat GeoJSON endpoints serve skeletons: position, name and the ids the client joins on.
Everything a popup shows — altitudes, countries, key col, parent, isolation, river — is
fetched from here, for the objects in the current viewport, and merged onto the features the
client already holds.

Two ways to ask:

    ?bbox=minLon,minLat,maxLon,maxLat[&limit=N]   the objects in a viewport, most
                                                 significant first
    ?pks=1,2,3                                   named objects, for a feature hovered
                                                 before its viewport batch arrived

At world zoom the bbox holds everything, so `limit` is what keeps the first request small;
ordering by prominence (or col depth) means the peaks a reader is likely to point at are the
ones that come back.
"""

import math

from django.contrib.gis.db.models import GeometryField
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db.models.functions import Cast
from django.core.exceptions import SuspiciousOperation
from django.http import JsonResponse
from django.views import View

DEFAULT_LIMIT = 300
MAX_LIMIT = 2000
MAX_PKS = 200


def parse_bbox(raw):
    """
    `minLon,minLat,maxLon,maxLat` in EPSG:4326 as a (Multi)Polygon, or None if not given.
    Longitudes may run past ±180°, as a map's view does when it crosses the antimeridian
    (170…195 over Fiji): they are taken round the globe, and a box across the antimeridian
    becomes two, one either side of it. A box 360° wide or more is the whole world. Plain
    rectangles in lon/lat: filter with `in_bbox`, which tests them flat, as a map draws them.
    """
    if not raw:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in raw.split(','))
    except ValueError:
        raise SuspiciousOperation('bbox must be four comma-separated numbers')
    if not (all(map(math.isfinite, (min_lon, min_lat, max_lon, max_lat)))
            and min_lon <= max_lon and -90 <= min_lat <= max_lat <= 90):
        raise SuspiciousOperation('bbox out of range')
    if max_lon - min_lon >= 360:
        min_lon, max_lon = -180.0, 180.0
    else:
        width = max_lon - min_lon
        min_lon = (min_lon + 180) % 360 - 180
        max_lon = min_lon + width
    spans = [(min_lon, max_lon)] if max_lon <= 180 else [(min_lon, 180.0), (-180.0, max_lon - 360)]
    pieces = [Polygon.from_bbox((a, min_lat, b, max_lat)) for a, b in spans]
    box = pieces[0] if len(pieces) == 1 else MultiPolygon(*pieces)
    box.srid = 4326
    return box


def in_bbox(queryset, field, box):
    """
    `queryset` narrowed to the objects whose `field` (a geography point, such as
    'point__location') lies in `box` (from `parse_bbox`). The test is flat, on longitude and
    latitude, through the index on the location cast to geometry: as geography the box's edges
    would be great circles, bowing towards the pole, and a world-wide box took seconds.
    """
    return (queryset.annotate(_flat_location=Cast(field, GeometryField(srid=4326)))
            .filter(_flat_location__intersects=box))


def parse_pks(raw):
    if not raw:
        return None
    try:
        pks = [int(part) for part in raw.split(',') if part]
    except ValueError:
        raise SuspiciousOperation('pks must be comma-separated integers')
    return pks[:MAX_PKS]


def parse_limit(raw):
    if not raw:
        return DEFAULT_LIMIT
    try:
        return max(1, min(int(raw), MAX_LIMIT))
    except ValueError:
        raise SuspiciousOperation('limit must be an integer')


class ViewportDetailView(View):
    """
    Subclasses supply `get_queryset()`, the path to the point to filter on, and the ordering
    that decides who wins when the viewport holds more objects than the limit.
    """

    location_field = 'point__location'
    ordering = ()

    def detail(self, obj):
        return obj.to_detail_dict()

    def rank_queryset(self):
        """
        A light queryset carrying only what `ordering` sorts by, or None. With it the viewport's
        winners are picked first and the popup queryset (distances, parents, a dozen joins) is
        built for those alone, not for every object in view: at world zoom that is 300 summits
        instead of 240 000, 0.1 s instead of 5.
        """
        return None

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        if (pks := parse_pks(request.GET.get('pks'))) is not None:
            queryset = queryset.filter(pk__in=pks)
        else:
            bbox = parse_bbox(request.GET.get('bbox'))
            # `intersects`, not `within`: for a point the two are the same, but Postgres
            # guesses that ST_Within keeps a single row, and plans the popup's dozen joins as
            # nested loops on that guess — 46 s for a view of Slovakia, against 0.1 s with the
            # estimate `intersects` gets right.
            inside = (lambda qs: in_bbox(qs, self.location_field, bbox)) if bbox is not None else (lambda qs: qs)
            limit = parse_limit(request.GET.get('limit'))
            rank = self.rank_queryset()
            if rank is not None:
                pks = list(inside(rank).order_by(*self.ordering).values_list('pk', flat=True)[:limit])
                queryset = queryset.filter(pk__in=pks).order_by(*self.ordering)
            else:
                queryset = inside(queryset).order_by(*self.ordering)[:limit]

        return JsonResponse({str(obj.pk): self.detail(obj) for obj in queryset})
