"""
Reports app views.

Report data is built server-side as structured datasets that are
rendered straight into templates. The same datasets back a CSV
export (format=csv) and the JSON report endpoints, so all three
output formats always agree.
"""
import csv

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncDate, Extract
from datetime import timedelta

from parking.models import ParkingSlot, ParkingSession, ParkingRate
from vehicles.models import Vehicle
from payments.models import Payment
from .models import ReportLog


REPORT_TITLES = {
    'daily': 'Daily Summary',
    'revenue': 'Revenue Report',
    'occupancy': 'Occupancy Report',
    'vehicle-history': 'Vehicle History',
    'payment-summary': 'Payment Summary',
}


def _int_param(request, name, default):
    """Read an integer query parameter, falling back to a default."""
    try:
        return int(request.GET.get(name, default))
    except (TypeError, ValueError):
        return default


def _build_daily_summary(request):
    """Build the daily summary dataset."""
    today = timezone.now().date()
    start_of_day = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    end_of_day = start_of_day + timedelta(days=1)

    # Sessions today
    sessions_today = ParkingSession.objects.filter(
        entry_time__gte=start_of_day,
        entry_time__lt=end_of_day
    )

    # Completed sessions today
    completed_today = sessions_today.filter(
        status=ParkingSession.Status.COMPLETED
    )

    # Revenue today
    payments_today = Payment.objects.filter(
        payment_time__gte=start_of_day,
        payment_time__lt=end_of_day,
        payment_status=Payment.PaymentStatus.PAID
    )

    total_revenue = payments_today.aggregate(Sum('amount'))['amount__sum'] or 0
    total_sessions = sessions_today.count()
    completed_sessions = completed_today.count()
    active_sessions = ParkingSession.objects.filter(
        status=ParkingSession.Status.ACTIVE
    ).count()

    # Occupancy
    total_slots = ParkingSlot.objects.count()
    occupied_slots = ParkingSlot.objects.filter(status=ParkingSlot.Status.OCCUPIED).count()
    occupancy_rate = (occupied_slots / total_slots * 100) if total_slots > 0 else 0

    # Average duration
    avg_duration = completed_today.aggregate(Avg('duration_minutes'))['duration_minutes__avg'] or 0

    # Vehicle type breakdown
    vehicle_types = sessions_today.values('vehicle__vehicle_type').annotate(
        count=Count('id')
    ).order_by('-count')

    data = {
        'date': today.isoformat(),
        'total_sessions': total_sessions,
        'completed_sessions': completed_sessions,
        'active_sessions': active_sessions,
        'total_revenue': float(total_revenue),
        'total_slots': total_slots,
        'occupied_slots': occupied_slots,
        'occupancy_rate': round(occupancy_rate, 2),
        'average_duration_minutes': round(avg_duration, 2),
        'vehicle_type_breakdown': list(vehicle_types),
    }

    # Log report
    ReportLog.objects.create(
        report_type=ReportLog.ReportType.DAILY_SUMMARY,
        parameters={'date': today.isoformat()},
    )

    return data


def _build_revenue(request):
    """Build the revenue report dataset for a date range."""
    days = _int_param(request, 'days', 7)
    end_date = timezone.now()
    start_date = end_date - timedelta(days=days)

    payments = Payment.objects.filter(
        payment_time__gte=start_date,
        payment_time__lte=end_date,
        payment_status=Payment.PaymentStatus.PAID
    )

    daily_revenue = payments.annotate(
        date=TruncDate('payment_time')
    ).values('date').annotate(
        total=Sum('amount'),
        count=Count('id')
    ).order_by('date')

    total_revenue = payments.aggregate(Sum('amount'))['amount__sum'] or 0
    total_transactions = payments.count()

    data = {
        'period_days': days,
        'start_date': start_date.date().isoformat(),
        'end_date': end_date.date().isoformat(),
        'total_revenue': float(total_revenue),
        'total_transactions': total_transactions,
        'average_per_transaction': float(total_revenue / total_transactions) if total_transactions > 0 else 0,
        'daily_breakdown': list(daily_revenue),
    }

    ReportLog.objects.create(
        report_type=ReportLog.ReportType.REVENUE,
        parameters={'days': days},
    )

    return data


def _build_occupancy(request):
    """Build the occupancy report dataset."""
    days = _int_param(request, 'days', 7)
    end_date = timezone.now()
    start_date = end_date - timedelta(days=days)

    total_slots = ParkingSlot.objects.count()

    # Get hourly occupancy for the period
    sessions = ParkingSession.objects.filter(
        entry_time__gte=start_date,
        entry_time__lte=end_date
    )

    # Current occupancy
    current_occupied = ParkingSlot.objects.filter(status=ParkingSlot.Status.OCCUPIED).count()
    current_occupancy_rate = (current_occupied / total_slots * 100) if total_slots > 0 else 0

    # Peak occupancy estimation
    peak_hourly = sessions.annotate(
        hour=Extract('entry_time', 'hour')
    ).values('hour').annotate(
        count=Count('id')
    ).order_by('-count')[:5]

    data = {
        'period_days': days,
        'total_slots': total_slots,
        'current_occupied': current_occupied,
        'current_occupancy_rate': round(current_occupancy_rate, 2),
        'peak_hours': list(peak_hourly),
    }

    ReportLog.objects.create(
        report_type=ReportLog.ReportType.OCCUPANCY,
        parameters={'days': days},
    )

    return data


def _build_vehicle_history(request):
    """
    Build the vehicle history dataset.

    Returns (data, error): exactly one of the two is set.
    """
    reg_number = request.GET.get('registration_number', '').strip().upper()

    if not reg_number:
        return None, 'registration_number is required'

    reg_clean = reg_number.replace(' ', '')
    if len(reg_clean) >= 4:
        reg_number = reg_clean[:3] + ' ' + reg_clean[3:]

    vehicle = Vehicle.objects.filter(registration_number=reg_number).first()

    if not vehicle:
        return None, 'Vehicle not found'

    sessions = vehicle.parking_sessions.all().order_by('-entry_time')

    total_sessions = sessions.count()
    completed_sessions = sessions.filter(status=ParkingSession.Status.COMPLETED).count()
    total_paid = sessions.aggregate(Sum('amount_due'))['amount_due__sum'] or 0
    avg_duration = sessions.filter(
        status=ParkingSession.Status.COMPLETED
    ).aggregate(Avg('duration_minutes'))['duration_minutes__avg'] or 0

    session_data = []
    for session in sessions:
        session_data.append({
            'session_id': session.id,
            'slot': session.slot.slot_number,
            'entry_time': session.entry_time,
            'exit_time': session.exit_time,
            'duration_minutes': session.duration_minutes,
            'amount_due': float(session.amount_due),
            'status': session.status,
        })

    data = {
        'vehicle': str(vehicle),
        'total_sessions': total_sessions,
        'completed_sessions': completed_sessions,
        'total_paid': float(total_paid),
        'average_duration_minutes': round(avg_duration, 2),
        'sessions': session_data,
    }

    ReportLog.objects.create(
        report_type=ReportLog.ReportType.VEHICLE_HISTORY,
        parameters={'registration_number': reg_number},
    )

    return data, None


def _build_payment_summary(request):
    """Build the payment summary dataset."""
    days = _int_param(request, 'days', 30)
    end_date = timezone.now()
    start_date = end_date - timedelta(days=days)

    payments = Payment.objects.filter(
        payment_time__gte=start_date,
        payment_time__lte=end_date
    )

    by_status = payments.values('payment_status').annotate(
        count=Count('id'),
        total=Sum('amount')
    ).order_by('-total')

    by_method = payments.filter(
        payment_status=Payment.PaymentStatus.PAID
    ).values('payment_method').annotate(
        count=Count('id'),
        total=Sum('amount')
    ).order_by('-total')

    total_paid = payments.filter(
        payment_status=Payment.PaymentStatus.PAID
    ).aggregate(Sum('amount'))['amount__sum'] or 0

    data = {
        'period_days': days,
        'total_paid': float(total_paid),
        'by_status': list(by_status),
        'by_method': list(by_method),
    }

    ReportLog.objects.create(
        report_type=ReportLog.ReportType.PAYMENT_SUMMARY,
        parameters={'days': days},
    )

    return data


# Each builder returns (dataset, error).
BUILDERS = {
    'daily': lambda request: (_build_daily_summary(request), None),
    'revenue': lambda request: (_build_revenue(request), None),
    'occupancy': lambda request: (_build_occupancy(request), None),
    'vehicle-history': _build_vehicle_history,
    'payment-summary': lambda request: (_build_payment_summary(request), None),
}


def _csv_response(report_type, data, request):
    """Stream a report dataset as a CSV attachment."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="parkflow-{report_type}-{timezone.localdate()}.csv"'
    )
    writer = csv.writer(response)

    if report_type == 'daily':
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Date', data['date']])
        writer.writerow(['Total Sessions', data['total_sessions']])
        writer.writerow(['Completed Sessions', data['completed_sessions']])
        writer.writerow(['Active Sessions', data['active_sessions']])
        writer.writerow(['Total Revenue (KSh)', data['total_revenue']])
        writer.writerow(['Occupancy Rate (%)', data['occupancy_rate']])
        writer.writerow(['Average Duration (min)', data['average_duration_minutes']])
        writer.writerow([])
        writer.writerow(['Vehicle Type', 'Count'])
        for row in data['vehicle_type_breakdown']:
            writer.writerow([row['vehicle__vehicle_type'], row['count']])

    elif report_type == 'revenue':
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Period (days)', data['period_days']])
        writer.writerow(['Start Date', data['start_date']])
        writer.writerow(['End Date', data['end_date']])
        writer.writerow(['Total Revenue (KSh)', data['total_revenue']])
        writer.writerow(['Total Transactions', data['total_transactions']])
        writer.writerow(['Average per Transaction (KSh)', data['average_per_transaction']])
        writer.writerow([])
        writer.writerow(['Date', 'Revenue (KSh)', 'Transactions'])
        for row in data['daily_breakdown']:
            writer.writerow([row['date'], row['total'], row['count']])

    elif report_type == 'occupancy':
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Period (days)', data['period_days']])
        writer.writerow(['Total Slots', data['total_slots']])
        writer.writerow(['Currently Occupied', data['current_occupied']])
        writer.writerow(['Occupancy Rate (%)', data['current_occupancy_rate']])
        writer.writerow([])
        writer.writerow(['Hour', 'Sessions'])
        for row in data['peak_hours']:
            writer.writerow([f"{row['hour']}:00", row['count']])

    elif report_type == 'vehicle-history':
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Vehicle', data['vehicle']])
        writer.writerow(['Total Sessions', data['total_sessions']])
        writer.writerow(['Completed Sessions', data['completed_sessions']])
        writer.writerow(['Total Paid (KSh)', data['total_paid']])
        writer.writerow(['Average Duration (min)', data['average_duration_minutes']])
        writer.writerow([])
        writer.writerow(['Session', 'Slot', 'Entry', 'Exit', 'Duration (min)', 'Amount (KSh)', 'Status'])
        for row in data['sessions']:
            writer.writerow([
                row['session_id'],
                row['slot'],
                row['entry_time'],
                row['exit_time'] or 'Active',
                row['duration_minutes'],
                row['amount_due'],
                row['status'],
            ])

    elif report_type == 'payment-summary':
        writer.writerow(['Metric', 'Value'])
        writer.writerow(['Period (days)', data['period_days']])
        writer.writerow(['Total Paid (KSh)', data['total_paid']])
        writer.writerow([])
        writer.writerow(['Status', 'Count', 'Total (KSh)'])
        for row in data['by_status']:
            writer.writerow([row['payment_status'], row['count'], row['total'] or 0])
        writer.writerow([])
        writer.writerow(['Method', 'Count', 'Total (KSh)'])
        for row in data['by_method']:
            writer.writerow([row['payment_method'], row['count'], row['total'] or 0])

    return response


def reports_dashboard(request):
    """
    Reports page: a plain GET form picks the report type, the dataset
    is rendered server-side, and the same dataset downloads as CSV.
    """
    report_type = request.GET.get('type', '').strip()

    context = {
        'report_type': None,
        'report_title': None,
        'report_error': None,
        'data': None,
        'generated_at': None,
        'csv_url': None,
    }

    if report_type in REPORT_TITLES:
        data, error = BUILDERS[report_type](request)

        if request.GET.get('format') == 'csv' and data:
            return _csv_response(report_type, data, request)

        context.update({
            'report_type': report_type,
            'report_title': REPORT_TITLES[report_type],
            'report_error': error,
            'data': data,
            'generated_at': timezone.localtime(),
        })
        if data:
            params = request.GET.copy()
            params['format'] = 'csv'
            context['csv_url'] = request.path + '?' + params.urlencode()

    return render(request, 'reports/dashboard.html', context)


def daily_summary(request):
    """JSON endpoint: daily summary report."""
    return JsonResponse({'success': True, 'data': _build_daily_summary(request)})


def revenue_report(request):
    """JSON endpoint: revenue report for a date range."""
    return JsonResponse({'success': True, 'data': _build_revenue(request)})


def occupancy_report(request):
    """JSON endpoint: occupancy report."""
    return JsonResponse({'success': True, 'data': _build_occupancy(request)})


def vehicle_history_report(request):
    """JSON endpoint: vehicle history report."""
    data, error = _build_vehicle_history(request)
    if error:
        return JsonResponse(
            {'success': False, 'message': error},
            status=400 if 'required' in error else 404,
        )
    return JsonResponse({'success': True, 'data': data})


def payment_summary_report(request):
    """JSON endpoint: payment summary report."""
    return JsonResponse({'success': True, 'data': _build_payment_summary(request)})
