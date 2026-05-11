from django.urls import path
from . import views

urlpatterns = [
    path('', views.summit.MountainListView.as_view(), name='mountain-list'),
    path('statistics', views.StatisticsView.as_view(), name='statistics'),

    path('prominence-tree', views.ProminenceForestView.as_view(), name='prominence-tree'),
    path('isolation-tree', views.IsolationForestView.as_view(), name='isolation-tree'),
    path('slope-tree', views.SlopeTreeView.as_view(), name='slope-tree'),
    path('horizon-forest', views.HorizonTreeView.as_view(), name='horizon-forest'),

    path('prominence', views.summit_map, name='map-prominence'),
    path('isolation', views.isolation_map, name='map-isolation'),

    path('summit/<int:pk>/', views.MountainDetailView.as_view(), name='summit-detail'),
    path('summit/<int:pk>/geo.json/', views.SummitDetailGeoJSON.as_view(), name='summit-detail-geojson'),
    path('summit/<int:pk>/prominence-lineage.json', views.summit.ProminenceLineageJson.as_view(),
         name='prominence-lineage-json'),
    path('summit/<int:pk>/isolation-lineage.json',  views.summit.IsolationLineageJson.as_view(),
         name='isolation-lineage-json'),

    path('col/<int:pk>/', views.ColView.as_view(), name='col'),

    path('confluences', views.confluence.ListView.as_view(), name='confluence-list'),
    path('confluence/<int:pk>/', views.confluence.DetailView.as_view(), name='confluence'),
]
