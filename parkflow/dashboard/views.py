"""
Dashboard app views.

Main dashboard for operators showing real-time parking status.
Data is refreshed from the Flask engine (common.mirror sync) and
served from the local reporting replica.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum

from parking.models import ParkingSlot, ParkingSession
from payments.models import Payment

from common import mirror


def _dashboard_payload():
    """
    Build the structured dashboard dataset.

    Shared by the server-rendered operator page (structured context)
    and the JSON API endpoint (serialised), so both always agree.
    """
    mirror.sync_slots()
    mirror.sync_sessions()

    # Slot summary
    slots = ParkingSlot.objects.all()
    total_slots = slots.count()
    available_slots = slots.filter(status=ParkingSlot.Status.AVAILABLE).count()
    occupied_slots = slots.filter(status=ParkingSlot.Status.OCCUPIED).count()
    maintenance_slots = slots.filter(status=ParkingSlot.Status.MAINTENANCE).count()

    # Active sessions (local replica, refreshed above)
    active_sessions = ParkingSession.objects.filter(
        status=ParkingSession.Status.ACTIVE
    ).select_related('vehicle', 'slot').order_by('entry_time')

    active_session_data = []
    for session in active_sessions:
        active_session_data.append({
            'session_id': session.id,
            'vehicle': str(session.vehicle),
            'slot': session.slot.slot_number,
            'entry_time': session.entry_time,
            'duration_minutes': session.calculate_duration(),
        })

    # Slot grid grouped by location (same shape as /dashboard/slots/grid/)
    locations = {}
    for slot in slots.order_by('slot_number'):
        loc = slot.location or 'General'
        locations.setdefault(loc, []).append({
            'slot_number': slot.slot_number,
            'status': slot.status,
        })

    # Today's stats
    today = timezone.now().date()
    start_of_day = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))

    sessions_today = ParkingSession.objects.filter(entry_time__gte=start_of_day).count()
    completed_today = ParkingSession.objects.filter(
        entry_time__gte=start_of_day,
        status=ParkingSession.Status.COMPLETED
    ).count()

    payments_today = Payment.objects.filter(
        payment_time__gte=start_of_day,
        payment_status=Payment.PaymentStatus.PAID
    )
    revenue_today = payments_today.aggregate(Sum('amount'))['amount__sum'] or 0

    return {
        'slots': {
            'total': total_slots,
            'available': available_slots,
            'occupied': occupied_slots,
            'maintenance': maintenance_slots,
            'occupancy_rate': round((occupied_slots / total_slots * 100) if total_slots > 0 else 0, 2),
        },
        'slot_grid': locations,
        'active_sessions': active_session_data,
        'stats_today': {
            'total_sessions': sessions_today,
            'completed_sessions': completed_today,
            'revenue': float(revenue_today),
        },
        'timestamp': timezone.now().isoformat(),
    }


def operator_dashboard(request):
    """Main operator dashboard view - rendered server-side from structured data."""
    data = _dashboard_payload()
    context = {
        'metrics': data['slots'],
        'slot_grid': data['slot_grid'],
        'active_sessions': data['active_sessions'],
        'stats_today': data['stats_today'],
        'generated_at': timezone.localtime(),
    }
    return render(request, 'dashboard/operator.html', context)


def dashboard_data(request):
    """API endpoint for real-time dashboard data."""
    data = _dashboard_payload()
    data['active_sessions'] = [
        dict(session, entry_time=session['entry_time'].isoformat())
        for session in data['active_sessions']
    ]

    return JsonResponse({'success': True, 'data': data})


def slot_grid(request):
    """Get slot grid data for visual display (refreshed from engine)."""
    mirror.sync_slots()

    slots = ParkingSlot.objects.all().order_by('slot_number')

    # Group by location for organized display
    locations = {}
    for slot in slots:
        loc = slot.location or 'General'
        if loc not in locations:
            locations[loc] = []
        locations[loc].append({
            'slot_number': slot.slot_number,
            'status': slot.status,
        })

    return JsonResponse({
        'success': True,
        'data': {
            'locations': locations,
        }
    })
