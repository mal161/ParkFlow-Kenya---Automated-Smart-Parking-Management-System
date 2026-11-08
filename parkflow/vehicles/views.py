"""
Vehicles app views.

Lookup and registration are proxied to the Flask parking engine
(Browser -> Django -> Flask -> Supabase) and mirrored locally; the
list and history pages read the local reporting replica.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from common import flask_client, mirror
from common.flask_client import API_PREFIX, FlaskAPIError
from common.http import BadInput, error, json_body, text_field
from common.plates import normalize_registration
from parking.models import ParkingSession

from .models import Vehicle

PAGE_SIZE = 50


@require_GET
def vehicle_list(request):
    """Registered vehicles (local replica), paginated, optional search."""
    query = request.GET.get('q', '').strip()
    vehicles = Vehicle.objects.annotate(
        active_session_count=Count(
            'parking_sessions',
            filter=Q(parking_sessions__status=ParkingSession.Status.ACTIVE),
        )
    ).order_by('-created_at')
    if query:
        # Match both the typed text ('123A') and its canonical form
        # ('kaa123' -> 'KAA 123').
        vehicles = vehicles.filter(
            Q(registration_number__icontains=query)
            | Q(registration_number__icontains=normalize_registration(query))
        )
    page = Paginator(vehicles, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'vehicles/list.html', {
        'vehicles': page,
        'page_obj': page,
        'query': query,
    })


@require_GET
def vehicle_lookup(request):
    """API endpoint: look up a vehicle via the engine."""
    reg_number = request.GET.get('registration_number', '').strip()
    if not reg_number:
        return error('Registration number is required')

    normalized = normalize_registration(reg_number)

    try:
        body, status = flask_client.get(
            f'{API_PREFIX}/vehicles/lookup?'
            + urlencode({'registration_number': normalized})
        )
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    # Registered locally but unknown to the engine: fall back to the
    # local copy so vehicles registered before the engine saw them resolve.
    if status == 404:
        vehicle = Vehicle.objects.filter(registration_number=normalized).first()
        if vehicle:
            active = vehicle.parking_sessions.filter(
                status=ParkingSession.Status.ACTIVE
            ).select_related('slot').first()
            data = {
                'registration_number': vehicle.registration_number,
                'vehicle_type': vehicle.vehicle_type,
                'created_at': vehicle.created_at.isoformat(),
                'has_active_session': active is not None,
            }
            if active:
                data['active_session'] = {
                    'session_id': active.id,
                    'vehicle': vehicle.registration_number,
                    'vehicle_type': vehicle.vehicle_type,
                    'slot': active.slot.slot_number,
                    'entry_time': active.entry_time.isoformat(),
                    'duration_minutes': active.calculate_duration(),
                    'status': active.status,
                    'amount_due': float(active.amount_due),
                }
            body, status = {'success': True, 'data': data}, 200

    return JsonResponse(body, status=status)


@require_POST
def vehicle_register(request):
    """
    Register a vehicle via the engine, then mirror it locally.

    Expected POST data: {"registration_number": "KAA 123A", "vehicle_type": "CAR"}
    """
    try:
        data = json_body(request)
        registration_number = text_field(data, 'registration_number', max_length=20)
        vehicle_type = text_field(data, 'vehicle_type', required=False, default='CAR').upper()
    except BadInput as exc:
        return error(str(exc))

    try:
        body, status = flask_client.post(f'{API_PREFIX}/vehicles/register', {
            'registration_number': registration_number,
            'vehicle_type': vehicle_type,
        })
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    if body.get('success'):
        mirror.mirror_vehicle(body['data']['registration_number'], vehicle_type)
        messages.success(request, body.get('message') or 'Vehicle registered')

    return JsonResponse(body, status=status)


@require_GET
def vehicle_history(request, registration_number):
    """Parking history page for a vehicle (local reporting replica)."""
    vehicle = get_object_or_404(
        Vehicle, registration_number=normalize_registration(registration_number)
    )
    sessions = vehicle.parking_sessions.select_related('slot').order_by('-entry_time')
    counts = sessions.aggregate(
        total=Count('id'),
        completed=Count('id', filter=Q(status=ParkingSession.Status.COMPLETED)),
        active=Count('id', filter=Q(status=ParkingSession.Status.ACTIVE)),
    )
    return render(request, 'vehicles/history.html', {
        'vehicle': vehicle,
        'sessions': sessions,
        'total_sessions': counts['total'],
        'completed_sessions': counts['completed'],
        'active_sessions': counts['active'],
    })
