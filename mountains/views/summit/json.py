import math
from typing import Any

from django.db.models import F
from django.http import JsonResponse
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

class SummitDetailGeoJSON(BaseDetailView):
    model = Summit
    queryset = Summit.objects.select_related(
        'point',
        'key_col__point',
        'key_col__confluence_river__source',
        'prominence_parent__point',
    ).annotate(
        prominence=F('point__altitude') - F('key_col__point__altitude'),
    )

    def get(self, request, *args, **kwargs):
        s = self.get_object()
        return JsonResponse({
            'type': 'FeatureCollection',
            'features': build_summit_features(s),
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
