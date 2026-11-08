"""
Dashboard app views.

Main dashboard for operators showing real-time parking status.
Data is refreshed from the Flask engine (common.mirror sync) and
served from the local reporting replica.
"""
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from common import mirror
from common.timeutils import local_day_bounds
from parking.models import ParkingSession, ParkingSlot
from payments.models import Payment


def _slot_grid():
    """Slots grouped by location, in slot-number order."""
    locations = {}
    for slot in ParkingSlot.objects.order_by('slot_number').only(
        'slot_number', 'status', 'location'
    ):
        locations.setdefault(slot.location or 'General', []).append({
            'slot_number': slot.slot_number,
            'status': slot.status,
        })
    return locations


def _dashboard_payload():
    """
    Build the structured dashboard dataset.

    Shared by the server-rendered operator page and the JSON endpoint,
    so both always agree.
    """
    mirror.sync_slots()
    mirror.sync_sessions()

    slot_counts = ParkingSlot.objects.aggregate(
        total=Count('id'),
        available=Count('id', filter=Q(status=ParkingSlot.Status.AVAILABLE)),
        occupied=Count('id', filter=Q(status=ParkingSlot.Status.OCCUPIED)),
        maintenance=Count('id', filter=Q(status=ParkingSlot.Status.MAINTENANCE)),
    )
    total_slots = slot_counts['total']

    active_sessions = [
        {
            'session_id': session.id,
            'vehicle': str(session.vehicle),
            'slot': session.slot.slot_number,
            'entry_time': session.entry_time,
            'duration_minutes': session.calculate_duration(),
        }
        for session in ParkingSession.objects.filter(
            status=ParkingSession.Status.ACTIVE
        ).select_related('vehicle', 'slot').order_by('entry_time')
    ]

    # "Today" is the local (Africa/Nairobi) calendar day.
    start_of_day, end_of_day = local_day_bounds()
    sessions_today = ParkingSession.objects.filter(
        entry_time__gte=start_of_day, entry_time__lt=end_of_day
    ).aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status=ParkingSession.Status.COMPLETED)),
    )
    revenue_today = Payment.objects.filter(
        payment_time__gte=start_of_day,
        payment_time__lt=end_of_day,
        payment_status=Payment.PaymentStatus.PAID,
    ).aggregate(total=Sum('amount'))['total'] or 0

    return {
        'slots': {
            'total': total_slots,
            'available': slot_counts['available'],
            'occupied': slot_counts['occupied'],
            'maintenance': slot_counts['maintenance'],
            'occupancy_rate': round(
                slot_counts['occupied'] / total_slots * 100 if total_slots else 0, 2
            ),
        },
        'slot_grid': _slot_grid(),
        'active_sessions': active_sessions,
        'stats_today': {
            'total_sessions': sessions_today['total'],
            'completed_sessions': sessions_today['completed'],
            'revenue': float(revenue_today),
        },
        'timestamp': timezone.now().isoformat(),
    }


@require_GET
def operator_dashboard(request):
    """Main operator dashboard - rendered server-side from structured data."""
    data = _dashboard_payload()
    return render(request, 'dashboard/operator.html', {
        'metrics': data['slots'],
        'slot_grid': data['slot_grid'],
        'active_sessions': data['active_sessions'],
        'stats_today': data['stats_today'],
        'generated_at': timezone.localtime(),
    })


@require_GET
def dashboard_data(request):
    """API endpoint for real-time dashboard data."""
    data = _dashboard_payload()
    data['active_sessions'] = [
        dict(session, entry_time=session['entry_time'].isoformat())
        for session in data['active_sessions']
    ]
    return JsonResponse({'success': True, 'data': data})


@require_GET
def slot_grid(request):
    """Slot grid data for visual display (refreshed from engine)."""
    mirror.sync_slots()
    return JsonResponse({'success': True, 'data': {'locations': _slot_grid()}})
