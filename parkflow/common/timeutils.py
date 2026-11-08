"""Time helpers shared by the dashboard and reports."""
from datetime import datetime, time, timedelta

from django.utils import timezone


def local_day_bounds(day=None):
    """
    (start, end) aware datetimes of a calendar day in the configured
    TIME_ZONE (Africa/Nairobi). Defaults to today.

    Using timezone.localdate() matters: timezone.now().date() is the UTC
    date, which is "yesterday" between 00:00 and 03:00 Nairobi time.
    """
    day = day or timezone.localdate()
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)
