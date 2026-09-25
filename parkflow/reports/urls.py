"""
Reports app URL configuration.
"""
from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.reports_dashboard, name='dashboard'),
    path('daily-summary/', views.daily_summary, name='daily_summary'),
    path('revenue/', views.revenue_report, name='revenue'),
    path('occupancy/', views.occupancy_report, name='occupancy'),
    path('vehicle-history/', views.vehicle_history_report, name='vehicle_history'),
    path('payment-summary/', views.payment_summary_report, name='payment_summary'),
]