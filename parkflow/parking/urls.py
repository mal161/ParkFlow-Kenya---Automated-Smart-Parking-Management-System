"""
Parking app URL configuration.
"""
from django.urls import path
from . import views

app_name = 'parking'

urlpatterns = [
    path('', views.parking_dashboard, name='dashboard'),
    path('availability/', views.slot_availability, name='availability'),
    path('entry/', views.vehicle_entry, name='entry'),
    path('exit/', views.vehicle_exit, name='exit'),
    path('sessions/active/', views.active_sessions, name='active_sessions'),
    path('sessions/<int:session_id>/', views.session_detail, name='session_detail'),
]