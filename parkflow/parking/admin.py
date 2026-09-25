"""
Parking app admin configuration.
"""
from django.contrib import admin
from .models import ParkingSlot, ParkingSession, ParkingRate


@admin.register(ParkingSlot)
class ParkingSlotAdmin(admin.ModelAdmin):
    list_display = ['slot_number', 'status', 'location', 'created_at']
    list_filter = ['status', 'location']
    search_fields = ['slot_number', 'location']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['slot_number']


@admin.register(ParkingSession)
class ParkingSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'vehicle', 'slot', 'entry_time', 'exit_time', 'duration_minutes', 'amount_due', 'status']
    list_filter = ['status', 'entry_time']
    search_fields = ['vehicle__registration_number', 'slot__slot_number']
    readonly_fields = ['created_at', 'updated_at', 'duration_minutes', 'amount_due']
    ordering = ['-entry_time']
    date_hierarchy = 'entry_time'


@admin.register(ParkingRate)
class ParkingRateAdmin(admin.ModelAdmin):
    list_display = ['vehicle_type', 'free_minutes', 'two_hour_rate', 'four_hour_rate', 'six_hour_rate', 'over_six_hour_rate', 'active', 'effective_from']
    list_filter = ['vehicle_type', 'active']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-effective_from']