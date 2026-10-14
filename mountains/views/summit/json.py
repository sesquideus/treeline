import math
from typing import Any

from django.db.models import Case, F, IntegerField, Value, When
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic.detail import BaseDetailView

from core.functions.world import distance
from mountains.models import Summit


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


def build_summit_features(s: Summit):
    features = []
    summit_coords = [s.point.location.x, s.point.location.y]

    # True point \u2014 delegate to the model serializer so the feature carries the
    # full property set (pk, prom, parents, kc, ...), then override the label.
    summit_feature = s.to_geojson()
    summit_feature['properties']['name'] = (
        f"\u26f0 {s.point.name} ({s.point.altitude:.1f} m)"
    )
    features.append(summit_feature)

    if s.nearest_higher_point:
        iso_coords = [s.nearest_higher_point.x, s.nearest_higher_point.y]
        iso_distance_true = distance(
            (s.point.location.y, s.point.location.x),
            (s.nearest_higher_point.y, s.nearest_higher_point.x),
        )

        if s.isolation_parent is not None:
            iso_label = f"\u21e5 {s.isolation_name} {s.isolation_parent.point.name} ({iso_distance_true.km:.3f} km)"
        else:
            iso_label = f"\u21e5 {s.isolation_name} ({iso_distance_true.km:.3f} km)"
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': iso_coords if iso_coords else None,
            },
            'properties': {'name': iso_label, 'type': 'isolation_point'},
        })

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': [iso_coords, summit_coords]},
            'properties': {'type': 'isolation_line_first'},
        })
        features.append({
            'type': 'Feature',
            'geometry': isolation_circle(s.point.location.y, s.point.location.x, iso_distance_true.m),
            'properties': {
                'type': 'isolation_circle',
                'name': f'Isolation radius: {iso_distance_true.km:.3f} km'
            },
        })
    else:
        iso_distance_true = None

    if s.isolation_parent:
        iso_distance_parent = distance(
            (s.point.location.y, s.point.location.x),
            (s.isolation_parent.point.location.y, s.isolation_parent.point.location.x),
        )
        parent_coords = [s.isolation_parent.point.location.x, s.isolation_parent.point.location.y]
        parent_name = f"{s.isolation_parent.point.name} ({s.isolation_parent.point.altitude:.1f} m)"

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': parent_coords},
            'properties': {
                'name': f"\u21e5 {s.isolation_parent.point.name} ({s.isolation_parent.point.altitude:.1f} m) "
                        f"({iso_distance_parent.km:.3f} km)",
                'type': 'isolation_parent'
            },
        })

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': [iso_coords, parent_coords]},
            'properties': {'type': 'isolation_line_second'},
        })
    else:
        parent_name = "unknown parent"

    if (pp := s.prominence_parent) is not None:
        parent_coords = [pp.point.location.x, pp.point.location.y]
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': parent_coords},
            'properties': {
                'name': f"\u2283 {pp.point.name} ({pp.point.altitude:.1f} m)",
                'type': 'prominence_parent',
            },
        })

    if s.key_col is not None:
        # Prime the reverse OneToOne cache so Col.to_dict() can read `key_for`
        # (and its `prominence` annotation) without an extra query.
        s.key_col.key_for = s
        col_coords = [s.key_col.point.location.x, s.key_col.point.location.y]
        col_feature = s.key_col.to_geojson()
        if col_feature is not None:
            # Override the bare name with the decorated, altitude-tagged label.
            col_feature['properties']['name'] = (
                f"\U0001F511 {s.key_col.point.name} ({s.key_col.point.altitude:.1f} m)"
            )
            features.append(col_feature)

    if s.key_col and s.key_col.point and s.prominence_parent and s.prominence_parent.point:
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'LineString',
                'coordinates': [summit_coords, col_coords, parent_coords],
            },
            'properties': {'type': 'prominence_line'},
        })

    if (ep := s.compute_encirclement_parent()) is not None:
        enc_coords = [ep.point.location.x, ep.point.location.y]
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': enc_coords},
            'properties': {'name': f"\u25CE {ep.point.name} ({ep.point.altitude:.1f} m)", 'type': 'encirclement_parent'},
        })
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': [summit_coords, enc_coords]},
            'properties': {'type': 'encirclement_line'},
        })

    return features

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
    model = Summit
    queryset = Summit.objects.select_related(
        'point',
        'key_col__point',
        'key_col__confluence_river__source',
        'key_col__confluence_river__parent__source',
        'prominence_parent__point',
    ).prefetch_related(
        'point__countries',
        'key_col__point__countries',
    ).annotate(
        prominence=F('point__altitude') - F('key_col__point__altitude'),
    )

    def get(self, request, *args, **kwargs):
        s = self.get_object()
        return JsonResponse({
            'type': 'FeatureCollection',
            'features': build_summit_features(s),
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
