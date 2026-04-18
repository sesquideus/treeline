from django.urls import path
from . import views

urlpatterns = [
    path('', views.ListView.as_view(), name='mountains'),
    path('mountain/<int:pk>/', views.MountainView.as_view(), name='mountain'),
    path('mountain/<int:pk>/map', views.summit_detail_map, name='mountain-map'),
    path('col/<int:pk>/', views.ColView.as_view(), name='col'),
    path('prominence', views.summit_map, name='prominence-tree'),
    path('isolation', views.isolation_map, name='isolation-tree'),
]
