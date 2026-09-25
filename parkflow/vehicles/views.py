"""
Vehicles app views.

Lookup and registration are proxied to the Flask parking engine
(Browser -> Django -> Flask -> Supabase) and mirrored locally; the
list and history pages read the local reporting replica.
"""
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json
from urllib.parse import quote

from .models import Vehicle
from parking.models import ParkingSession

from common import flask_client, mirror
from common.flask_client import API_PREFIX, FlaskAPIError


def _normalize(reg: str) -> str:
    clean = reg.replace(' ', '').upper()
    if len(clean) >= 4:
        return clean[:3] + ' ' + clean[3:]
    return clean


def vehicle_list(request):
    """Display list of all registered vehicles (local replica)."""
    vehicles = Vehicle.objects.all().order_by('-created_at')
    return render(request, 'vehicles/list.html', {'vehicles': vehicles})


def vehicle_lookup(request):
    """API endpoint: look up a vehicle via the engine."""
    reg_number = request.GET.get('registration_number', '').strip()

    if not reg_number:
        return JsonResponse({
            'success': False,
            'message': 'Registration number is required'
        }, status=400)

    normalized = _normalize(reg_number)

    try:
        body, status = flask_client.get(
            f'{API_PREFIX}/vehicles/lookup'
            f'?registration_number={quote(normalized)}'
        )
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    # Registered locally but never parked: fall back to the local copy
    # so vehicles registered before the engine saw them still resolve.
    if status == 404:
        vehicle = Vehicle.objects.filter(registration_number=normalized).first()
        if vehicle:
            active = vehicle.parking_sessions.filter(
                status=ParkingSession.Status.ACTIVE
            ).first()
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
            body = {'success': True, 'data': data}
            status = 200

    return JsonResponse(body, status=status)


@require_http_methods(["POST"])
def vehicle_register(request):
    """
    Register a vehicle via the engine, then mirror it locally.

    Expected POST data:
    {
        "registration_number": "KAA 123A",
        "vehicle_type": "CAR"
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON data'
        }, status=400)

    registration_number = data.get('registration_number', '').strip()
    vehicle_type = data.get('vehicle_type', 'CAR').upper()

    if not registration_number:
        return JsonResponse({
            'success': False,
            'message': 'Registration number is required'
        }, status=400)

    try:
        body, status = flask_client.post(
            f'{API_PREFIX}/vehicles/register',
            {'registration_number': registration_number,
             'vehicle_type': vehicle_type},
        )
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    if body.get('success'):
        mirror.mirror_vehicle(
            body['data']['registration_number'], vehicle_type
        )

    return JsonResponse(body, status=status)


def vehicle_history(request, registration_number):
    """Get parking history for a vehicle (local reporting replica)."""
    normalized = _normalize(registration_number)
    vehicle = get_object_or_404(Vehicle, registration_number=normalized)

    sessions = vehicle.parking_sessions.all().order_by('-entry_time')

    data = []
    for session in sessions:
        data.append({
            'session_id': session.id,
            'slot': session.slot.slot_number,
            'entry_time': session.entry_time.isoformat(),
            'exit_time': session.exit_time.isoformat() if session.exit_time else None,
            'duration_minutes': session.duration_minutes,
            'amount_due': float(session.amount_due),
            'status': session.status,
        })

    return JsonResponse({
        'success': True,
        'data': {
            'vehicle': str(vehicle),
            'sessions': data,
            'total_sessions': len(data),
        }
    })
