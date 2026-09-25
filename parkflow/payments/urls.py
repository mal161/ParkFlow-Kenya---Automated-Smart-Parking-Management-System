"""
Payments app URL configuration.
"""
from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    path('', views.payment_list, name='list'),
    path('calculate-fee/', views.calculate_fee, name='calculate_fee'),
    path('process/', views.process_payment, name='process'),
    path('session/<int:session_id>/history/', views.payment_history, name='history'),
]