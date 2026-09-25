"""
Payments app admin configuration.
"""
from django.contrib import admin
from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['transaction_reference', 'session', 'amount', 'payment_method', 'payment_status', 'payment_time']
    list_filter = ['payment_method', 'payment_status']
    search_fields = ['transaction_reference', 'session__vehicle__registration_number']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-payment_time']
    date_hierarchy = 'payment_time'