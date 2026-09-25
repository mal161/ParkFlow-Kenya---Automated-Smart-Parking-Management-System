"""
Parking app views - thin proxies to the Flask parking engine.

Architecture: Browser -> Django -> Flask -> Supabase.
Django no longer runs allocation or fee algorithms itself: workflow
calls are forwarded to the Flask API and successful results are
mirrored into the local reporting database (see common.mirror).
"""
import json

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from parking.models import ParkingSlot, ParkingSession

from common import flask_client, mirror
from common.flask_client import API_PREFIX, FlaskAPIError


def parking_dashboard(request):
    """
    Display the main parking management dashboard.

    Data comes from the local reporting replica, which is refreshed
    from the Flask engine on every page load (covers queue-served
    sessions and admin slot edits too).
    """
    mirror.sync_slots()
    mirror.sync_sessions()

    slots = ParkingSlot.objects.all().order_by('slot_number')
    active_sessions = ParkingSession.objects.filter(
        status=ParkingSession.Status.ACTIVE
    ).select_related('vehicle', 'slot').order_by('-entry_time')

    context = {
        'slots': slots,
        'active_sessions': active_sessions,
        'available_count': slots.filter(status=ParkingSlot.Status.AVAILABLE).count(),
        'occupied_count': slots.filter(status=ParkingSlot.Status.OCCUPIED).count(),
        'total_slots': slots.count(),
    }
    return render(request, 'parking/dashboard.html', context)


def slot_availability(request):
    """API endpoint: current slot availability from the engine."""
    try:
        body, status = flask_client.get(f'{API_PREFIX}/availability')
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)
    return JsonResponse(body, status=status)


@require_http_methods(["POST"])
def vehicle_entry(request):
    """
    Handle vehicle entry - forward to the engine, mirror the result.

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
            f'{API_PREFIX}/parking/entry',
            {'registration_number': registration_number,
             'vehicle_type': vehicle_type},
        )
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    if body.get('success') and body.get('data', {}).get('session_id'):
        mirror.mirror_entry(registration_number, vehicle_type, body['data'])

    return JsonResponse(body, status=status)


@require_http_methods(["POST"])
def vehicle_exit(request):
    """
    Handle vehicle exit - record exit time and calculate the fee.

    Expected POST data:
    {
        "registration_number": "KAA 123A",
        "exit_time": "2026-09-25T17:30:00"  // optional, default now
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
    if not registration_number:
        return JsonResponse({
            'success': False,
            'message': 'Registration number is required'
        }, status=400)

    payload = {'registration_number': registration_number}
    if data.get('exit_time'):
        payload['exit_time'] = data['exit_time']

    try:
        body, status = flask_client.post(
            f'{API_PREFIX}/parking/exit', payload,
        )
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    if body.get('success') and body.get('data', {}).get('session_id'):
        mirror.mirror_exit(body['data'])

    return JsonResponse(body, status=status)


def active_sessions(request):
    """Get all active parking sessions from the engine."""
    try:
        body, status = flask_client.get(f'{API_PREFIX}/parking/sessions/active')
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)
    return JsonResponse(body, status=status)


def session_detail(request, session_id):
    """Get details of a specific parking session from the engine."""
    try:
        body, status = flask_client.get(f'{API_PREFIX}/parking/session/{session_id}')
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    # Present the flat shape this endpoint has always returned.
    if body.get('success') and 'session' in body.get('data', {}):
        detail = body['data']
        session = detail['session']
        vehicle = detail.get('vehicle') or {}
        slot = detail.get('slot') or {}
        body = {
            'success': True,
            'data': {
                'session_id': session['session_id'],
                'vehicle': (
                    f"{vehicle.get('registration_number', session['vehicle_id'])}"
                    f" ({vehicle.get('vehicle_type', 'CAR')})"
                ),
                'slot': slot.get('slot_number', session['slot_id']),
                'entry_time': session['entry_time'],
                'exit_time': session.get('exit_time'),
                'duration_minutes': session.get('duration_minutes', 0),
                'amount_due': session.get('amount_due', 0),
                'status': session.get('status'),
            },
        }
    return JsonResponse(body, status=status)
