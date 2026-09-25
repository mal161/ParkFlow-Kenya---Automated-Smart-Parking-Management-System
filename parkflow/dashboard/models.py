"""
Dashboard app models.

This module defines models for dashboard configuration and widgets.
"""
from django.db import models
from django.utils import timezone


class DashboardWidget(models.Model):
    """
    Configurable dashboard widget for operators.

    Attributes:
        name: Display name of the widget
        widget_type: Type of widget (chart, table, metric, status)
        data_source: Identifier for the data source
        configuration: JSON configuration for the widget
        position: Display position on dashboard
        is_active: Whether the widget is currently displayed
        created_at: When the widget was created
        updated_at: When the widget was last updated
    """

    class WidgetType(models.TextChoices):
        METRIC = 'METRIC', 'Metric Card'
        CHART = 'CHART', 'Chart'
        TABLE = 'TABLE', 'Data Table'
        STATUS_GRID = 'STATUS_GRID', 'Status Grid'
        RECENT_ACTIVITY = 'RECENT_ACTIVITY', 'Recent Activity'

    name = models.CharField(max_length=100)
    widget_type = models.CharField(
        max_length=20,
        choices=WidgetType.choices,
        default=WidgetType.METRIC
    )
    data_source = models.CharField(max_length=100)
    configuration = models.JSONField(default=dict, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dashboard_widgets'
        ordering = ['position', 'name']

    def __str__(self):
        return f"{self.name} ({self.widget_type})"