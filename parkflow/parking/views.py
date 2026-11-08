"""
Parking app views - thin proxies to the Flask parking engine.

Architecture: Browser -> Django -> Flask -> Supabase.
Django does not run allocation or fee algorithms itself: workflow
calls are forwarded to the Flask API and successful results are
mirrored into the local reporting database (see common.mirror).
"""
from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from common import flask_client, mirror
from common.flask_client import API_PREFIX, FlaskAPIError
from common.http import BadInput, error, json_body, text_field
from parking.models import ParkingSlot, ParkingSession


def _proxy(method, path, payload=None):
    """Call the engine; map 'engine down' to the uniform 503 body."""
    try:
        return flask_client.request(method, path, payload)
    except FlaskAPIError as exc:
        return flask_client.unavailable(exc)


@require_GET
def parking_dashboard(request):
    """
    Main parking management page.

    Data comes from the local reporting replica, refreshed from the Flask
    engine on every page load (covers queue-served sessions and admin
    slot edits too).
    """
    mirror.sync_slots()
    mirror.sync_sessions()

    slots = ParkingSlot.objects.order_by('slot_number')
    context = {
        'slots': slots,
        'active_sessions': ParkingSession.objects.filter(
            status=ParkingSession.Status.ACTIVE
        ).select_related('vehicle', 'slot').order_by('-entry_time'),
        'available_count': slots.filter(status=ParkingSlot.Status.AVAILABLE).count(),
        'occupied_count': slots.filter(status=ParkingSlot.Status.OCCUPIED).count(),
        'total_slots': slots.count(),
    }
    return render(request, 'parking/dashboard.html', context)


@require_GET
def slot_availability(request):
    """API endpoint: current slot availability from the engine."""
    body, status = _proxy('GET', f'{API_PREFIX}/availability')
    return JsonResponse(body, status=status)


@require_POST
def vehicle_entry(request):
    """
    Handle vehicle entry - forward to the engine, mirror the result.

    Expected POST data: {"registration_number": "KAA 123A", "vehicle_type": "CAR"}
    """
    try:
        data = json_body(request)
        registration_number = text_field(data, 'registration_number', max_length=20)
        vehicle_type = text_field(data, 'vehicle_type', required=False, default='CAR').upper()
    except BadInput as exc:
        return error(str(exc))

    body, status = _proxy('POST', f'{API_PREFIX}/parking/entry', {
        'registration_number': registration_number,
        'vehicle_type': vehicle_type,
    })

    if body.get('success') and body.get('data', {}).get('session_id'):
        mirror.mirror_entry(registration_number, vehicle_type, body['data'])
        # Shown after the browser reloads the page.
        messages.success(request, body.get('message') or 'Vehicle entry recorded')

    return JsonResponse(body, status=status)


@require_POST
def vehicle_exit(request):
    """
    Handle vehicle exit - the engine records the exit time (now) and
    calculates duration and fee.

    Expected POST data: {"registration_number": "KAA 123A"}
    """
    try:
        data = json_body(request)
        registration_number = text_field(data, 'registration_number', max_length=20)
    except BadInput as exc:
        return error(str(exc))

    payload = {'registration_number': registration_number}
    # Client-chosen exit times are only honoured for automated e2e tests.
    if settings.ALLOW_EXIT_TIME_OVERRIDE and data.get('exit_time'):
        payload['exit_time'] = data['exit_time']

    body, status = _proxy('POST', f'{API_PREFIX}/parking/exit', payload)

    result = body.get('data') or {}
    if body.get('success') and result.get('session_id'):
        mirror.mirror_exit(result)
        if not result.get('payment_required'):
            # Free parking: the browser reloads straight away. Paid
            # exits confirm later, after the payment.
            messages.success(request, body.get('message') or 'Vehicle exit recorded')

    return JsonResponse(body, status=status)


@require_GET
def active_sessions(request):
    """All active parking sessions from the engine."""
    body, status = _proxy('GET', f'{API_PREFIX}/parking/sessions/active')
    return JsonResponse(body, status=status)


@require_GET
def session_detail(request, session_id):
    """Details of one parking session from the engine (flat shape)."""
    body, status = _proxy('GET', f'{API_PREFIX}/parking/session/{session_id}')

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
