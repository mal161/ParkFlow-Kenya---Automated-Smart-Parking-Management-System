"""
Vehicles app admin configuration.
"""
from django.contrib import admin
from .models import Vehicle


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ['registration_number', 'vehicle_type', 'created_at']
    list_filter = ['vehicle_type']
    search_fields = ['registration_number']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']