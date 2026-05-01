from django.urls import path
from . import views

urlpatterns = [
    path('', views.ProminenceForestView.as_view(), name='prominence-forest'),
    path('isolation-forest', views.IsolationForestView.as_view(), name='isolation-forest'),
    path('prominence', views.summit_map, name='map-prominence'),
    path('isolation', views.isolation_map, name='map-isolation'),
    path('summit/<int:pk>/', views.MountainDetailView.as_view(), name='summit-detail'),
    path('summit/<int:pk>/geo.json/', views.SummitDetailGeoJSON.as_view(), name='summit-detail-geojson'),
    path('summit/<int:pk>/prominence-lineage.json', views.summit.ProminenceLineageJson.as_view(),
         name='prominence-lineage-json'),
    path('summit/<int:pk>/isolation-lineage.json',  views.summit.IsolationLineageJson.as_view(),
         name='isolation-lineage-json'),
    path('col/<int:pk>/', views.ColView.as_view(), name='col'),
]
