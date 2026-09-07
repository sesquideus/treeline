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

from django.contrib.gis.geos import Polygon
from django.core.exceptions import SuspiciousOperation
from django.http import JsonResponse
from django.views import View

DEFAULT_LIMIT = 300
MAX_LIMIT = 2000
MAX_PKS = 200


def parse_bbox(raw):
    """`minLon,minLat,maxLon,maxLat` in EPSG:4326 as a Polygon, or None if not given."""
    if not raw:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in raw.split(','))
    except ValueError:
        raise SuspiciousOperation('bbox must be four comma-separated numbers')
    if not (-180 <= min_lon <= max_lon <= 180 and -90 <= min_lat <= max_lat <= 90):
        raise SuspiciousOperation('bbox out of range')
    return Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))


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

    def get(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        if (pks := parse_pks(request.GET.get('pks'))) is not None:
            queryset = queryset.filter(pk__in=pks)
        else:
            if (bbox := parse_bbox(request.GET.get('bbox'))) is not None:
                queryset = queryset.filter(**{f'{self.location_field}__within': bbox})
            queryset = queryset.order_by(*self.ordering)[:parse_limit(request.GET.get('limit'))]

        return JsonResponse({str(obj.pk): self.detail(obj) for obj in queryset})
