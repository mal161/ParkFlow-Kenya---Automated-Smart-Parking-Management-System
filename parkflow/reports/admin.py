"""
Reports app admin configuration.
"""
from django.contrib import admin
from .models import ReportLog


@admin.register(ReportLog)
class ReportLogAdmin(admin.ModelAdmin):
    list_display = ['report_type', 'generated_by', 'generated_at']
    list_filter = ['report_type']
    search_fields = ['generated_by']
    readonly_fields = ['generated_at']
    ordering = ['-generated_at']
    date_hierarchy = 'generated_at'