import math
from typing import Any

from django.db.models import F, Prefetch
from django.http import JsonResponse
from django.views.generic import ListView, DetailView
from django.views.generic.detail import BaseDetailView

from core.functions.world import distance
from ..models import Summit


def isolation_circle(lat, lon, radius, steps=256) -> dict[str, Any]:
    """Return a GeoJSON Polygon approximating a geodesic circle."""
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


def build_feature(name, coords, *, geom_type, prop_type):
    return {
        'type': 'Feature',
        'geometry': {
            'type': geom_type,
            'coordinates': coords
        },
        'properties': {
            'name': name,
            'type': prop_type,
        }
    }


def build_point(name, coords, *, type):
    return build_feature(
        name=name,
        coords=coords,
        geom_type='Point',
        prop_type=type,
    )


def build_summit_features(s):
    features = []
    summit_coords = [s.point.longitude, s.point.latitude]

    # True point
    features.append(
        build_point(f"\u26f0 {s.point.name} ({s.point.altitude:.1f} m)", summit_coords, type='summit')
    )

    if s.isolation_latitude and s.isolation_longitude:
        iso_coords = [s.isolation_longitude, s.isolation_latitude]
        iso_distance_true = distance(
            (s.point.latitude, s.point.longitude),
            (s.isolation_latitude, s.isolation_longitude),
        )
        if s.isolation_parent is not None:
            iso_label = f"\u21e5 {s.isolation_name} {s.isolation_parent.point.name} ({iso_distance_true.km:.3f} km)"
        else:
            iso_label = f"\u21e5 {s.isolation_name} ({iso_distance_true.km:.3f} km)"
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': iso_coords},
            'properties': {'name': iso_label, 'type': 'isolation_point'},
        })

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': [iso_coords, summit_coords]},
            'properties': {'type': 'isolation_line_first'},
        })
        features.append({
            'type': 'Feature',
            'geometry': isolation_circle(s.point.latitude, s.point.longitude, iso_distance_true.m),
            'properties': {
                'type': 'isolation_circle',
                'name': f'Isolation radius: {iso_distance_true.km:.3f} km'
            },
        })
    else:
        iso_distance_true = None

    if s.isolation_parent:
        iso_distance_parent = distance(
            (s.point.latitude, s.point.longitude),
            (s.isolation_parent.point.latitude, s.isolation_parent.point.longitude),
        )
        parent_coords = [s.isolation_parent.point.longitude, s.isolation_parent.point.latitude]
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
        parent_coords = [pp.point.longitude, pp.point.latitude]
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': parent_coords},
            'properties': {
                'name': f"\u2283 {pp.point.name} ({pp.point.altitude:.1f} m)",
                'type': 'prominence_parent',
            },
        })

    if s.key_col is not None:
        col_coords    = [s.key_col.point.longitude, s.key_col.point.latitude]
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': col_coords},
            'properties': {
                'name': f"\U0001F511 {s.key_col.point.name} ({s.key_col.point.altitude:.1f} m)",
                'type': 'col'
            },
        })

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
        enc_coords = [ep.point.longitude, ep.point.latitude]
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


class ProminenceForestView(ListView):
    model = Summit
    context_object_name = 'mountains'
    template_name = 'mountains/prominence-forest.html'

    def get_queryset(self):
        mountains = Summit.objects.with_prominence().select_related('key_col__point', 'point')

        self.mountain_map = {}
        for mountain in mountains:
            self.mountain_map.setdefault(mountain.prominence_parent_id, []).append(mountain)

        for key, value in self.mountain_map.items():
            self.mountain_map[key] = sorted(value, key=lambda m: (m.prominence or 0), reverse=True)

        self.roots = self.mountain_map.get(None, [])
        return mountains


    def get_context_data(self, object_list=None, **kwargs):
        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'mountain_map': self.mountain_map,
        }


class IsolationForestView(ListView):
    model = Summit
    context_object_name = 'mountains'
    template_name = 'mountains/isolation-forest.html'

    def get_queryset(self):
        mountains = Summit.objects.select_related('isolation_parent__point', 'point')

        self.tree = {}
        for mountain in mountains:
            self.tree.setdefault(mountain.isolation_parent_id, []).append(mountain)

        for key, value in self.tree.items():
            self.tree[key] = sorted(value, key=lambda m: (m.isolation() or 0), reverse=True)

        self.roots = self.tree.get(None, [])
        return mountains

    def get_context_data(self, object_list=None, **kwargs):
        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'mountain_map': self.tree,
        }


class MountainDetailView(DetailView):
    model = Summit
    context_object_name = 'mountain'
    template_name = 'mountains/summit.html'

    def get_queryset(self):
        return Summit.objects.with_prominence().select_related('point').prefetch_related(
            Prefetch('prominence_children',
                     queryset=Summit.objects.with_prominence().select_related('key_col__point').order_by('-prominence'))
        )

class SummitDetailGeoJSON(BaseDetailView):
    model = Summit
    queryset = Summit.objects.select_related(
        'point',
        'key_col__point',
        'prominence_parent__point',
        'encirclement_parent__point',
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