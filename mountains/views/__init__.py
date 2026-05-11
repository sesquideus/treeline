import json

from django.shortcuts import render, get_object_or_404

from core.functions.world import distance
from ..models import Summit

from .summit import (ProminenceForestView, IsolationForestView, MountainDetailView, SlopeTreeView, HorizonTreeView,
                     SummitDetailGeoJSON)
from .col import ColView
from . import confluence
from .statistics import StatisticsView



def build_line(peak, col, parent, kind: str):
    name = f"{peak.point.name} ({peak.point.altitude} m) \u2198 {peak.prominence():.1f} m \u2198 {col.point.name} ({col.point.altitude:.1f} m) \u2197 {parent.point.name}"
    if kind == 'down':
        p1, p2 = peak, col
    else:
        p1, p2 = col, parent

    return {
        'type': 'Feature',
        'geometry': {
            'type': 'LineString',
            'coordinates': [
                [p1.point.longitude, p1.point.latitude],
                [p2.point.longitude, p2.point.latitude],
            ]
        },
        'properties': {
            'type': f'prominence_line_{kind}',
            'from': p1.point.name,
            'to': p2.point.name,
            'name': name,
        }
    }


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
            features.append(build_line(s, col, parent, 'down'))
            features.append(build_line(s, col, parent, 'up'))

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
                'name': f"{s.point.name} ({s.point.altitude} m)",
                'type': 'summit',
            }
        })

        if s.isolation_latitude and s.isolation_longitude:
            dist = distance(
                (s.point.latitude, s.point.longitude),
                (s.isolation_latitude, s.isolation_longitude),
            )
            if s.isolation_parent:
                iso_label = (
                    f"{s.isolation_name} of {s.isolation_parent.point.name} "
                    f"({dist.km:.3} km)"
                )
            else:
                iso_label = f"{dist} km"

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
        dist = distance(
            (s.point.latitude, s.point.longitude),
            (s.isolation_latitude, s.isolation_longitude),
        ) * 1000
        parent_name = (
            s.isolation_parent.point.name
            if s.isolation_parent and s.isolation_parent.point
            else 'Unknown'
        )
        iso_label = f"{s.isolation_name or parent_name} of {s.point.name} ({dist} km)"

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
            'geometry': isolation_circle(s.point.latitude, s.point.longitude, dist),
            'properties': {'type': 'isolation_circle', 'name': f'Isolation radius: {dist} km'},
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
