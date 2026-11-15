import math
from typing import Any

from django.db.models import Case, IntegerField, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic.detail import BaseDetailView

from mountains.models import Summit
from mountains.views.detail_map import summit_features, summit_map_queryset


def isolation_circle(lat, lon, radius, steps=256) -> dict[str, Any]:
    """Return a GeoJSON Polygon approximating a geodesic circle."""
    # FixMe: make this proper with Vincenty formula (r, a)
    coords = []
    R = 6371000  # Earth radius in km
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    d = radius / R  # angular distance

    for i in range(steps + 1):
        bearing = math.radians(i * 360 / steps)
        lat2 = math.asin(
            math.sin(lat_r) * math.cos(d) +
            math.cos(lat_r) * math.sin(d) * math.cos(bearing)
        )
        lon2 = lon_r + math.atan2(
            math.sin(bearing) * math.sin(d) * math.cos(lat_r),
            math.cos(d) - math.sin(lat_r) * math.sin(lat2)
        )
        coords.append([math.degrees(lon2), math.degrees(lat2)])

    return {
        'type': 'Polygon',
        'coordinates': [coords]
    }


#: What each mark on the comparison map says it is, without needing a click. The peaks
#: under comparison, the summits on the way up from each, the one they meet at, and the
#: cols the lines are drawn through.
COMPARED_MARK = '\u26f0'          # mountain
ANCESTRY_MARK = '\u2283'          # superset of
MEETING_MARK = '\u22d4'           # pitchfork: two chains joining
KEY_COL_MARK = '\U0001f511'       # key


def summit_mark(summit, kind, glyph):
    """One summit on the comparison map, with a label and a link to its own page."""
    return {
        'type': 'Feature',
        'geometry': {
            'type': 'Point',
            'coordinates': [summit.point.location.x, summit.point.location.y],
        },
        'properties': {
            'type': kind,
            'name': f'{glyph} {summit.point.display_name()} ({summit.point.altitude:.1f} m)',
            'url': summit.get_absolute_url(),
            # Read by `summitMarker()` for the two peaks under comparison; the chain's own
            # marks are a flat blue and ignore it.
            'prom': summit.compute_prominence(),
        },
    }


def col_mark(col, separating):
    """
    A col the chain is drawn through.

    The lowest one is called out by name: it is the saddle that actually separates the two
    peaks, the same col the page tables it under "Lowest connecting col", and on a chain of
    a dozen saddles it is the one worth finding.
    """
    note = ' \u2014 lowest connecting col' if separating else ''
    return {
        'type': 'Feature',
        'geometry': {
            'type': 'Point',
            'coordinates': [col.point.location.x, col.point.location.y],
        },
        'properties': {
            'type': 'col',
            'name': (f'{KEY_COL_MARK} {col.point.display_name()} '
                     f'({col.point.altitude:.1f} m){note}'),
            'url': col.get_absolute_url(),
        },
    }


def ancestry_line(child, col, parent):
    """
    One link of the chain: a summit to its prominence parent, by way of the key col between
    them when there is one. The col is the middle vertex, which is what the map's "route
    through key col" checkbox drops and restores.
    """
    coordinates = [[child.point.location.x, child.point.location.y]]
    if col is not None:
        coordinates.append([col.point.location.x, col.point.location.y])
    coordinates.append([parent.point.location.x, parent.point.location.y])
    return {
        'type': 'Feature',
        'geometry': {'type': 'LineString', 'coordinates': coordinates},
        'properties': {'type': 'prominence_line'},
    }


def build_comparison_features(s1, s2):
    """
    Two peaks and the prominence chains that join them, for the map on the compare page.

    The picture is one walk: up from the first peak, over the summit both descend from, and
    back down to the second. Everything on it is drawn — each summit, each key col the line
    routes through, and the segments between — so the common ancestor is not just a name in
    a table but a place with two chains visibly meeting at it.

    Peaks in different trees yield just the two marks. That is a real answer here, not a
    failure: this is a forest with a root per landmass, and two peaks on different ones have
    no common ancestor to draw.

    When one peak *is* the other's ancestor, it keeps its own mark as a compared peak rather
    than gaining the meeting glyph — the chain simply ends on it, and the page's own table
    is what names it.
    """
    features = [summit_mark(s1, 'summit', COMPARED_MARK)]
    if s2.pk != s1.pk:
        features.append(summit_mark(s2, 'summit', COMPARED_MARK))

    ancestor, mine, theirs = s1.common_prominence_ancestry(s2)
    if ancestor is None:
        return features

    if ancestor.pk not in (s1.pk, s2.pk):
        features.append(summit_mark(ancestor, 'common_ancestor', MEETING_MARK))

    cols = [summit.key_col for summit in (*mine, *theirs)
            if summit.key_col and summit.key_col.point]
    lowest = min(cols, key=lambda col: col.point.altitude, default=None)

    for chain in (mine, theirs):
        for summit in chain[1:]:
            features.append(summit_mark(summit, 'prominence_parent', ANCESTRY_MARK))

        walk = [*chain, ancestor]
        for child, parent in zip(walk, walk[1:]):
            col = child.key_col if child.key_col and child.key_col.point else None
            if col is not None:
                features.append(col_mark(col, separating=col.pk == lowest.pk))
            features.append(ancestry_line(child, col, parent))

    return features


class SummitDetailGeoJSON(BaseDetailView):
    """The summit page's map, as JSON — the page itself embeds the same collection."""
    model = Summit

    def get_queryset(self):
        return summit_map_queryset()

    def get(self, request, *args, **kwargs):
        s = self.get_object()
        return JsonResponse({
            'type': 'FeatureCollection',
            'features': summit_features(s),
        })


class ComparisonGeoJson(View):
    """
    The map behind the compare page: both peaks and the chains that join them.

    A plain `View` rather than a `BaseDetailView` because it is about a pair, and `get_object`
    has room for only one. `select_related` covers the two ends; the chains load themselves,
    one step at a time, in `prominence_walk()`.
    """

    def get(self, request, pk1, pk2):
        summits = Summit.objects.select_related('point', 'key_col__point')
        return JsonResponse({
            'type': 'FeatureCollection',
            'features': build_comparison_features(get_object_or_404(summits, pk=pk1),
                                                  get_object_or_404(summits, pk=pk2)),
        })


class ProminenceLineageJson(BaseDetailView):
    model = Summit
    queryset = Summit.objects.select_related('point', 'key_col__point')

    def get(self, request, *args, **kwargs):
        s = self.get_object()
        return JsonResponse({
            'summit': s.to_dict(),
            'ancestors': s.prominence_ancestors(),
            'children': s.prominence_children_list(),
        })


class IsolationLineageJson(BaseDetailView):
    model = Summit
    queryset = Summit.objects.select_related('point')

    def get(self, request, *args, **kwargs):
        s = self.get_object()
        return JsonResponse({
            'summit': s.to_dict(),
            'ancestors': s.isolation_ancestors(),
            'children': s.isolation_children_list(),
        })


class SummitSearch(View):
    """
    Summits whose name contains what has been typed, for the compare page's search boxes.

    The two pickers used to be `<select>`s holding all 2410 summits — a fifth of a megabyte
    of options on every render, twice over, and no way to find anything but by scrolling.
    This answers the same question the admin's autocomplete does, in the same shape: a short
    list, best match first.

    Names that start with the query come before names that merely contain it, because that
    is the order a reader expects when they type the first few letters of a mountain. A
    query of one character is not answered at all: it would match hundreds and mean nothing.
    """

    LIMIT = 12
    MINIMUM = 2

    def get(self, request):
        query = request.GET.get('q', '').strip()
        return JsonResponse({'results': [{'pk': summit.pk,
                                          'name': summit.point.name,
                                          'altitude': summit.point.altitude}
                                         for summit in self.matches(query)]})

    def matches(self, query):
        if len(query) < self.MINIMUM:
            return []
        return (Summit.objects.select_related('point')
                .filter(point__name__icontains=query)
                .annotate(rank=Case(When(point__name__istartswith=query, then=Value(0)),
                                    default=Value(1), output_field=IntegerField()))
                .order_by('rank', 'point__name')[:self.LIMIT])
