import json

from django.shortcuts import render, get_object_or_404

from core.functions.world import distance
from .summit.json import isolation_circle
from ..models import Summit

from .summit import (ProminenceForestView, IsolationForestView, MountainDetailView, SlopeTreeView, HorizonTreeView,
                     SummitDetailGeoJSON, SummitCompareView)
from .river import river
from . import confluence, col
from .statistics import StatisticsView


def build_line(peak, col, parent, kind: str):
    """
    Build a line from peak and col
    """
    name = (f"{peak.point.name} ({peak.point.altitude} m) "
            f"\u2198 {peak.compute_prominence():.1f} m \u2198 {col.point.name} "
            f"({col.point.altitude:.1f} m) \u2197 {parent.point.name}")
    if kind == 'down':
        p1, p2 = peak, col
    else:
        p1, p2 = col, parent

    return {
        'type': 'Feature',
        'geometry': {
            'type': 'LineString',
            'coordinates': [
                [p1.point.location.x, p1.point.location.y],
                [p2.point.location.x, p2.point.location.y],
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
    ).filter(point__location__isnull=False)

    features = []
    seen_cols = set()


    for s in summits:
        features.append({
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': [s.point.location.x, s.point.location.y],
            },
            'properties': {
                'name': f"{s.point.name} ({s.point.altitude} m)",
                'type': 'summit',
            }
        })

        # Col point — deduplicate since multiple summits may share a col
        col = s.key_col
        if col and col.pk not in seen_cols and col.point.location:
            seen_cols.add(col.pk)
            features.append({
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [col.point.location.x, col.point.location.y],
                },
                'properties': {
                    'name': f'{col.point.name} ({col.point.altitude} m)' ,
                    'type': 'col',
                }
            })

        # Line: summit → col → prominence parent
        parent = s.prominence_parent
        if col and col.point.location and parent and parent.point:
            features.append(build_line(s, col, parent, 'down'))
            features.append(build_line(s, col, parent, 'up'))

    geojson = json.dumps({'type': 'FeatureCollection', 'features': features})
    return render(request, 'mountains/map.html', {'geojson': geojson})


def map(request):
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
                'coordinates': [s.point.location.x, s.point.location.y],
            },
            'properties': {
                'name': f"{s.point.name} ({s.point.altitude} m)",
                'type': 'summit',
            }
        })

        if s.nearest_higher_point:
            dist = distance(
                (s.point.location.y, s.point.location.x),
                (s.nearest_higher_point.y, s.nearest_higher_point.x),
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
                    'coordinates': [s.nearest_higher_point.x, s.nearest_higher_point.y],
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
                        [s.point.location.x, s.point.location.y],
                        [s.nearest_higher_point.x, s.nearest_higher_point.y],
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
                            [s.nearest_higher_point.x, s.nearest_higher_point.y],
                            [s.isolation_parent.point.location.x, s.isolation_parent.point.location.y],
                        ]
                    },
                    'properties': {
                        'type': 'isolation_line_second',
                        'from': s.isolation_name or '',
                        'to': s.isolation_parent.point.name,
                    }
                })

    geojson = json.dumps({'type': 'FeatureCollection', 'features': features}, ensure_ascii=False)
    return render(request, 'mountains/maps/isolation_map.html', {'geojson': geojson})


def summit_detail_map(request, pk):
    s = get_object_or_404(
        Summit.objects.select_related(
            'point',
            'key_col__point',
            'prominence_parent__point',
        ),
        pk=pk
    )

    features = []

    # The summit itself
    summit_coords = [s.point.location.x, s.point.location.y]
    features.append({
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': summit_coords},
        'properties': {'name': s.point.name, 'type': 'summit'},
    })

    # Isolation: line from isolation position to summit, plus isolation circle
    if s.isolation_location.y and s.isolation_location.x:
        iso_coords = [s.isolation_location.x, s.isolation_location.y]
        dist = distance(
            (s.point.location.y, s.point.location.x),
            (s.isolation_location.y, s.isolation_location.x),
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
            parent_coords = [s.isolation_parent.point.location.x, s.isolation_parent.point.location.y]
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
            'geometry': isolation_circle(s.point.location.y, s.point.location.x, dist),
            'properties': {'type': 'isolation_circle', 'name': f'Isolation radius: {dist} km'},
        })

    # Prominence: summit → key col → prominence parent
    if s.key_col and s.key_col.point and s.prominence_parent and s.prominence_parent.point:
        col_coords    = [s.key_col.point.location.x, s.key_col.point.location.y]
        parent_coords = [s.prominence_parent.point.location.x, s.prominence_parent.point.location.y]
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
        enc_coords = [s.encirclement_parent.point.location.x, s.encirclement_parent.point.location.y]
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
