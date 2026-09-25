"""
Dashboard app admin configuration.
"""
from django.contrib import admin
from .models import DashboardWidget


@admin.register(DashboardWidget)
class DashboardWidgetAdmin(admin.ModelAdmin):
    list_display = ['name', 'widget_type', 'data_source', 'position', 'is_active']
    list_filter = ['widget_type', 'is_active']
    search_fields = ['name', 'data_source']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['position', 'name']