"""
Dashboard app URL configuration.
"""
from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.operator_dashboard, name='operator'),
    path('data/', views.dashboard_data, name='data'),
    path('slots/grid/', views.slot_grid, name='slot_grid'),
]