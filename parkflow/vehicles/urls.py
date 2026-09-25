"""
Vehicles app URL configuration.
"""
from django.urls import path
from . import views

app_name = 'vehicles'

urlpatterns = [
    path('', views.vehicle_list, name='list'),
    path('lookup/', views.vehicle_lookup, name='lookup'),
    path('register/', views.vehicle_register, name='register'),
    path('<str:registration_number>/history/', views.vehicle_history, name='history'),
]