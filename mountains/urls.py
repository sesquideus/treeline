from django.urls import path
from . import views

urlpatterns = [
    path('', views.summit.MountainListView.as_view(), name='mountain-list'),
    path('statistics', views.StatisticsView.as_view(), name='statistics'),

    path('prominence-tree', views.ProminenceForestView.as_view(), name='prominence-tree'),
    path('isolation-tree', views.IsolationForestView.as_view(), name='isolation-tree'),
    path('slope-tree', views.SlopeTreeView.as_view(), name='slope-tree'),
    path('horizon-forest', views.HorizonTreeView.as_view(), name='horizon-forest'),

    path('summits/geo.json', views.summit.GeoJsonView.as_view(), name='summits-geojson'),

    path('prominence', views.summit_map, name='map-prominence'),
    path('prominence-tree.json', views.summit.tree.ProminenceJsonView.as_view(), name='prominence-tree-json'),

    path('isolation', views.isolation_map, name='map-isolation'),
    path('isolation-tree.json', views.summit.tree.IsolationJsonView.as_view(), name='isolation-tree-json'),

    path('rivers/geo.json', views.river.GeoJsonView.as_view(), name='rivers-geojson'),
    path('cols/geo.json/', views.col.GeoJsonView.as_view(), name='cols-geojson'),

    path('summit/<int:pk>/', views.MountainDetailView.as_view(), name='summit-detail'),
    path('summit/<int:pk>/geo.json/', views.SummitDetailGeoJSON.as_view(), name='summit-detail-geojson'),
    path('summit/<int:pk>/prominence-lineage.json',
         views.summit.ProminenceLineageJson.as_view(),
         name='prominence-lineage-json'),
    path('summit/<int:pk>/isolation-lineage.json',
         views.summit.IsolationLineageJson.as_view(),
         name='isolation-lineage-json'),
    path('summit/compare/', views.SummitCompareView.as_view(), name='summit-compare'),
    path('summit/compare/<int:pk1>/<int:pk2>/', views.SummitCompareView.as_view(), name='summit-compare'),

    path('col/<int:pk>/', views.col.DetailView.as_view(), name='col'),
    path('cols', views.col.ListView.as_view(), name='col-list'),

    path('river/<int:pk>/', views.river.DetailView.as_view(), name='river-detail'),
    path('rivers', views.river.ListView.as_view(), name='river-list'),

    path('confluences', views.confluence.ListView.as_view(), name='confluence-list'),
    path('confluence/<int:pk>/', views.confluence.DetailView.as_view(), name='confluence'),
]
