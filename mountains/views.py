import json
import math
from collections import OrderedDict

from django.db.models import F, Prefetch
from django.shortcuts import render, get_object_or_404
from django.views.generic import ListView, DetailView
from vincenty import vincenty

from .models import Summit, Col


class ListView(ListView):
    model = Summit
    context_object_name = 'mountains'
    template_name = 'mountains/mountain-list.html'

    def get_queryset(self):
        mountains = Summit.objects.select_related('key_col__point', 'point')

        self.mountain_map = {}
        for mountain in mountains:
            self.mountain_map.setdefault(mountain.prominence_parent_id, []).append(mountain)

        for key, value in self.mountain_map.items():
            self.mountain_map[key] = sorted(value, key=lambda m: (m.prominence() or 0), reverse=True)

        self.roots = self.mountain_map.get(None, [])
        return mountains


    def get_context_data(self, object_list=None, **kwargs):
        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'mountain_map': self.mountain_map,
        }


class MountainView(DetailView):
    model = Summit
    context_object_name = 'mountain'
    template_name = 'mountains/mountain.html'

    def get_queryset(self):
        return super().get_queryset().select_related('point').prefetch_related(
            Prefetch('prominence_children',
                     queryset=Summit.objects.with_prominence().select_related('key_col__point').order_by('-prominence'))
        )


class ColView(DetailView):
    model = Col
    context_object_name = 'col'
    template_name = 'mountains/col.html'

    def get_queryset(self):
        return super().get_queryset().select_related('point')


def summit_map(request):
    summits = Summit.objects.select_related(
        'prominence_parent',
        'key_col',
    ).filter(point__latitude__isnull=False, point__longitude__isnull=False)

    features = []
    seen_cols = set()

    for s in summits:
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [s.point.longitude, s.point.latitude],
            },
            'properties': {
                'name': f"{s.point.name} ({s.point.altitude} m)",
                'type': 'summit',
            }
        })

        # Col point — deduplicate since multiple summits may share a col
        col = s.key_col
        if col and col.pk not in seen_cols and col.point.latitude and col.point.longitude:
            seen_cols.add(col.pk)
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [col.point.longitude, col.point.latitude],
                },
                'properties': {
                    'name': f'{col.point.name} ({col.point.altitude} m)' ,
                    'type': 'col',
                }
            })

        # Line: summit → col → prominence parent
        parent = s.prominence_parent
        if col and col.point.latitude and col.point.longitude and parent and parent.point.latitude and parent.point.longitude:
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [
                        [s.point.longitude, s.point.latitude],
                        [col.point.longitude, col.point.latitude],
                        [parent.point.longitude, parent.point.latitude],
                    ]
                },
                'properties': {
                    'type': 'prominence_line',
                    'from': s.point.name,
                    'to': parent.point.name,
                    'name': f"{s.point.name} \u2198 {s.key_col.point.name} \u2197 {s.prominence_parent.point.name}",
                }
            })

    geojson = json.dumps({'type': 'FeatureCollection', 'features': features})
    return render(request, 'mountains/map.html', {'geojson': geojson})


def isolation_map(request):
    summits = Summit.objects.select_related(
        'point',
        'isolation_parent__point',
    ).filter(point__isnull=False)

    features = []

    for s in summits:
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [s.point.longitude, s.point.latitude],
            },
            'properties': {
                'name': s.point.name,
                'type': 'summit',
            }
        })

        if s.isolation_latitude and s.isolation_longitude:
            distance = vincenty(
                (s.point.latitude, s.point.longitude),
                (s.isolation_latitude, s.isolation_longitude),
            )
            if s.isolation_parent:
                iso_label = (
                    f"{s.isolation_name or 'near'} "
                    f"{s.isolation_parent.point.name} "
                    f"({distance} km)"
                )
            else:
                iso_label = f"{distance} km"

            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [s.isolation_longitude, s.isolation_latitude],
                },
                'properties': {
                    'name': iso_label,
                    'type': 'isolation_point',
                }
            })

            # First leg: summit → isolation point (thick green)
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [
                        [s.point.longitude, s.point.latitude],
                        [s.isolation_longitude, s.isolation_latitude],
                    ]
                },
                'properties': {
                    'type': 'isolation_line_first',
                    'from': s.point.name,
                    'to': s.isolation_name or '',
                }
            })

            # Second leg: isolation point → isolation parent summit (thick purple)
            if s.isolation_parent and s.isolation_parent.point:
                features.append({
                    'type': 'Feature',
                    'geometry': {
                        'type': 'LineString',
                        'coordinates': [
                            [s.isolation_longitude, s.isolation_latitude],
                            [s.isolation_parent.point.longitude, s.isolation_parent.point.latitude],
                        ]
                    },
                    'properties': {
                        'type': 'isolation_line_second',
                        'from': s.isolation_name or '',
                        'to': s.isolation_parent.point.name,
                    }
                })

    geojson = json.dumps({'type': 'FeatureCollection', 'features': features}, ensure_ascii=False)
    return render(request, 'mountains/isolation_map.html', {'geojson': geojson})


def isolation_circle(lat, lon, radius, steps=64):
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

    return {'type': 'Polygon', 'coordinates': [coords]}


def summit_detail_map(request, pk):
    s = get_object_or_404(
        Summit.objects.select_related(
            'point',
            'key_col__point',
            'prominence_parent__point',
            'encirclement_parent__point',
        ),
        pk=pk
    )

    features = []

    # The summit itself
    summit_coords = [s.point.longitude, s.point.latitude]
    features.append({
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': summit_coords},
        'properties': {'name': s.point.name, 'type': 'summit'},
    })

    # Isolation: line from isolation position to summit, plus isolation circle
    if s.isolation_latitude and s.isolation_longitude:
        iso_coords = [s.isolation_longitude, s.isolation_latitude]
        distance = vincenty(
            (s.point.latitude, s.point.longitude),
            (s.isolation_latitude, s.isolation_longitude),
        ) * 1000
        parent_name = (
            s.isolation_parent.point.name
            if s.isolation_parent and s.isolation_parent.point
            else 'Unknown'
        )
        iso_label = f"{s.isolation_name or parent_name} of {s.point.name} ({distance} km)"

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': iso_coords},
            'properties': {'name': iso_label, 'type': 'isolation_point'},
        })

        # Line: isolation position → summit (green)
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': [iso_coords, summit_coords]},
            'properties': {'type': 'isolation_line_first'},
        })

        # Line: summit → isolation position (second leg, yellow→purple if parent known)
        if s.isolation_parent and s.isolation_parent.point:
            parent_coords = [s.isolation_parent.point.longitude, s.isolation_parent.point.latitude]
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': parent_coords},
                'properties': {'name': s.isolation_parent.point.name, 'type': 'isolation_parent'},
            })
            features.append({
                'type': 'Feature',
                'geometry': {'type': 'LineString', 'coordinates': [iso_coords, parent_coords]},
                'properties': {'type': 'isolation_line_second'},
            })

        # Isolation circle — approximated as a GeoJSON polygon
        features.append({
            'type': 'Feature',
            'geometry': isolation_circle(s.point.latitude, s.point.longitude, distance),
            'properties': {'type': 'isolation_circle', 'name': f'Isolation radius: {distance} km'},
        })

    # Prominence: summit → key col → prominence parent
    if s.key_col and s.key_col.point and s.prominence_parent and s.prominence_parent.point:
        col_coords    = [s.key_col.point.longitude, s.key_col.point.latitude]
        parent_coords = [s.prominence_parent.point.longitude, s.prominence_parent.point.latitude]
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': col_coords},
            'properties': {'name': s.key_col.point.name, 'type': 'col'},
        })
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': parent_coords},
            'properties': {'name': s.prominence_parent.point.name, 'type': 'prominence_parent'},
        })
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'LineString',
                'coordinates': [summit_coords, col_coords, parent_coords],
            },
            'properties': {'type': 'prominence_line'},
        })

    # Encirclement parent
    if s.encirclement_parent and s.encirclement_parent.point:
        enc_coords = [s.encirclement_parent.point.longitude, s.encirclement_parent.point.latitude]
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': enc_coords},
            'properties': {'name': s.encirclement_parent.point.name, 'type': 'encirclement_parent'},
        })
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': [summit_coords, enc_coords]},
            'properties': {'type': 'encirclement_line'},
        })

    geojson = json.dumps({'type': 'FeatureCollection', 'features': features}, ensure_ascii=False)
    return render(request, 'mountains/detail-map.html', {
        'geojson': geojson,
        'summit': s,
    })