"""
Reports app views.

Handles report generation and data aggregation for management.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Count, Sum, Avg
from django.db.models.functions import TruncDate, Extract
from datetime import timedelta

from parking.models import ParkingSlot, ParkingSession, ParkingRate
from vehicles.models import Vehicle
from payments.models import Payment
from .models import ReportLog


def reports_dashboard(request):
    """Display the reports dashboard."""
    return render(request, 'reports/dashboard.html')


def daily_summary(request):
    """Generate daily summary report."""
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

    return JsonResponse({'success': True, 'data': data})


def revenue_report(request):
    """Generate revenue report for a date range."""
    days = int(request.GET.get('days', 7))
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

    return JsonResponse({'success': True, 'data': data})


def occupancy_report(request):
    """Generate occupancy report."""
    days = int(request.GET.get('days', 7))
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

    return JsonResponse({'success': True, 'data': data})


def vehicle_history_report(request):
    """Generate vehicle history report."""
    reg_number = request.GET.get('registration_number', '').strip().upper()

    if not reg_number:
        return JsonResponse({
            'success': False,
            'message': 'registration_number is required'
        }, status=400)

    reg_clean = reg_number.replace(' ', '')
    if len(reg_clean) >= 4:
        reg_number = reg_clean[:3] + ' ' + reg_clean[3:]

    vehicle = Vehicle.objects.filter(registration_number=reg_number).first()

    if not vehicle:
        return JsonResponse({
            'success': False,
            'message': 'Vehicle not found'
        }, status=404)

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
            'entry_time': session.entry_time.isoformat(),
            'exit_time': session.exit_time.isoformat() if session.exit_time else None,
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

    return JsonResponse({'success': True, 'data': data})


def payment_summary_report(request):
    """Generate payment summary report."""
    days = int(request.GET.get('days', 30))
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

    return JsonResponse({'success': True, 'data': data})