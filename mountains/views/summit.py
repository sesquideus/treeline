import math
from abc import abstractmethod, ABC
from typing import Any

from cairn.views import OrderableListView
from django.db.models import F, Prefetch
from django.http import JsonResponse
from django.views.generic import ListView, DetailView
from django.views.generic.detail import BaseDetailView

from core.functions.world import distance
from core.models import Country
from ..models import Summit


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


class SummitTreeView(ListView, ABC):
    model = Summit
    context_object_name = 'mountains'
    reverse = True

    @staticmethod
    def sort_function(summit):
        return summit

    @staticmethod
    def parent_fk(summit):
        return summit

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.summits = []
        self.roots = []
        self.tree = {}

    def preprocess(self):
        pass

    def process(self):
        self.summits = self.get_queryset()

        self.preprocess()

        for mountain in self.summits:
            self.tree.setdefault(self.parent_fk(mountain), []).append(mountain)

        for key, value in self.tree.items():
            self.tree[key] = sorted(value, key=self.sort_function, reverse=self.reverse)

        self.roots = self.tree.get(None, [])

    def get_context_data(self, object_list=None, **kwargs):
        # Inject the preprocessing step here
        self.process()

        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'tree': self.tree,
        }


class ProminenceForestView(SummitTreeView):
    template_name = 'mountains/summit/prominence/tree.html'

    @staticmethod
    def sort_function(summit):
        return summit.prominence or 0

    @staticmethod
    def parent_fk(summit):
        return summit.prominence_parent_id

    def get_queryset(self):
        return Summit.objects.with_prominence().select_related('key_col__point', 'point')


class IsolationForestView(SummitTreeView):
    template_name = 'mountains/summit/isolation/tree.html'

    @staticmethod
    def sort_function(summit):
        return summit.compute_isolation() or 0

    @staticmethod
    def parent_fk(summit):
        return summit.isolation_parent_id

    def get_queryset(self):
        return Summit.objects.select_related('isolation_parent__point', 'point')



class SlopeTreeView(SummitTreeView):
    template_name = 'mountains/summit/slope/tree.html'
    reverse = False

    @staticmethod
    def sort_function(summit):
        return summit.point.slope_to(summit.slope_parent.point) if summit.slope_parent else -math.inf

    @staticmethod
    def parent_fk(summit):
        return summit.slope_parent_id

    def preprocess(self):
        for mountain in self.summits:
            mountain.slope = mountain.point.slope_to(mountain.slope_parent.point) if mountain.slope_parent else None

    def get_queryset(self):
        return Summit.objects.select_related('slope_parent__point', 'point')

    def get_context_data(self, object_list=None, **kwargs):
        return super().get_context_data(object_list=object_list, **kwargs) | {
            'roots': self.roots,
            'mountain_map': self.tree,
        }


class HorizonTreeView(SummitTreeView):
    template_name = 'mountains/summit/horizon/tree.html'

    @staticmethod
    def sort_function(summit):
        return summit.point.angle_to(summit.horizon_parent.point) if summit.horizon_parent else -90

    @staticmethod
    def parent_fk(summit):
        return summit.horizon_parent_id

    def preprocess(self):
        for mountain in self.summits:
            mountain.hhp_angle = mountain.point.angle_to(mountain.horizon_parent.point) if mountain.horizon_parent else None

    def get_queryset(self):
        return Summit.objects.select_related('horizon_parent__point', 'point')


class MountainDetailView(DetailView):
    model = Summit
    context_object_name = 'mountain'
    template_name = 'mountains/summit/detail.html'

    def get_queryset(self):
        return Summit.objects.with_prominence().with_isolation().with_slope_parent().prefetch_related(
            Prefetch('prominence_children',
                     queryset=Summit.objects.with_prominence().select_related('key_col__point').order_by('-prominence'))
        )

    def get_context_data(self, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        summits = Summit.objects.all().exclude(id=self.object.id).select_related('point')

        for s in summits:
            s.slope = self.object.point.slope_to(s.point)
            s.hhp_angle = self.object.point.angle_to(s.point)
            s.dh = s.point.altitude - self.object.point.altitude
            s.distance = s.point.distance_to(self.object.point)

        return context | {
            'by_slope': sorted(summits, key=lambda x: x.slope, reverse=True)[:20],
            'by_horizon': sorted(summits, key=lambda x: x.hhp_angle, reverse=True)[:20],
        }

class MountainListView(OrderableListView):
    model = Summit
    context_object_name = 'mountains'
    template_name = 'mountains/summit/list.html'

    ORDERING = {
        'name': 'point__name',
        'altitude': 'point__altitude',
        'prominence': 'prominence',
        'key-col': 'key_col__point__name',
        'key-col-alt': 'key_col__point__altitude',
        'nhn': 'isolation_parent__point__name',
        'isolation': 'isolation',
    }

    def get_queryset(self, *, countries=None):
        countries = Country.objects.filter(code='sk')
        qs = Summit.objects.with_prominence().with_isolation().with_slope_parent().with_countries()

        if self.ordering:
            if self.ordering[0] == '-':
                ordering = self.ordering[1:]
                qs = qs.order_by(F(ordering).desc(nulls_last=True))
            else:
                ordering = self.ordering
                qs = qs.order_by(F(ordering).asc(nulls_last=True))

        if countries is not None:
            qs = qs.filter(point__countries__in=countries)

        return qs


class SlopeToView(DetailView):
    model = Summit
    context_object_name = 'mountains'

    def get_context_data(self, object_list=None, **kwargs):
        context = super().get_context_data(object_list=object_list, **kwargs)
        return context | {
            'summits': Summit.objects.all()
        }


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