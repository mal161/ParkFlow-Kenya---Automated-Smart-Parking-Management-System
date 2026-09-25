"""
Reports app models.

This module defines models for report generation and logging.
"""
from django.db import models
from django.utils import timezone


class ReportLog(models.Model):
    """
    Log of generated reports for audit trail.

    Attributes:
        report_type: Type of report generated
        generated_by: User who generated the report (if auth is implemented)
        parameters: JSON string of report parameters
        generated_at: When the report was generated
        file_path: Path to generated report file (if applicable)
    """

    class ReportType(models.TextChoices):
        DAILY_SUMMARY = 'DAILY_SUMMARY', 'Daily Summary'
        REVENUE = 'REVENUE', 'Revenue Report'
        OCCUPANCY = 'OCCUPANCY', 'Occupancy Report'
        VEHICLE_HISTORY = 'VEHICLE_HISTORY', 'Vehicle History'
        PAYMENT_SUMMARY = 'PAYMENT_SUMMARY', 'Payment Summary'

    report_type = models.CharField(
        max_length=30,
        choices=ReportType.choices
    )
    generated_by = models.CharField(max_length=100, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(default=timezone.now)
    file_path = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'report_logs'
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['report_type']),
            models.Index(fields=['generated_at']),
        ]

    def __str__(self):
        return f"{self.report_type} report at {self.generated_at}"