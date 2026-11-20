from django.shortcuts import render

from .summit import (ProminenceForestView, IsolationForestView, MountainDetailView, SlopeTreeView, HorizonTreeView,
                     SummitDetailGeoJSON, SummitCompareView)
from .river import river
from . import col, map_features, point, range
from .statistics import StatisticsView


def map(request):
    """
    The global map page. Everything on it is fetched from the flat GeoJSON endpoints by
    `initGlobalMap()`; the view itself only renders the shell. It used to build a whole
    FeatureCollection here — summits, isolation points and both isolation legs, one geodesic
    per summit — and pass it as `geojson`, which no template has read for a long time.
    """
    return render(request, 'mountains/maps/isolation_map.html')
