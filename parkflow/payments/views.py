"""
Payments app views.

Fee calculation and payment processing are proxied to the Flask
parking engine (Browser -> Django -> Flask -> Supabase); successful
payments are mirrored into the local reporting database. The list
and history pages read the local replica.
"""
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
import json

from parking.models import ParkingSession
from .models import Payment

from common import flask_client, mirror
from common.flask_client import API_PREFIX, FlaskAPIError


def payment_list(request):
    """Display list of all payments (local reporting replica)."""
    payments = Payment.objects.select_related(
        'session__vehicle', 'session__slot'
    ).order_by('-payment_time')
    return render(request, 'payments/list.html', {'payments': payments})


def calculate_fee(request):
    """
    Calculate the parking fee for a session via the engine.

    Query parameters:
    - session_id: ID of the parking session
    - duration_minutes: Optional override for duration calculation
    """
    session_id = request.GET.get('session_id')
    duration_override = request.GET.get('duration_minutes')

    if not session_id:
        return JsonResponse({
            'success': False,
            'message': 'session_id is required'
        }, status=400)

    payload = {'session_id': int(session_id)}
    if duration_override:
        try:
            payload['duration_minutes'] = int(duration_override)
        except ValueError:
            return JsonResponse({
                'success': False,
                'message': 'duration_minutes must be an integer'
            }, status=400)

    try:
        body, status = flask_client.post(f'{API_PREFIX}/parking/fee', payload)
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)
    return JsonResponse(body, status=status)


@require_http_methods(["POST"])
def process_payment(request):
    """
    Process a cash payment via the engine, then mirror it locally.

    Expected POST data:
    {
        "session_id": 1,
        "amount": 50,
        "payment_method": "CASH",
        "transaction_reference": "CASH-123456"  // optional
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'message': 'Invalid JSON data'
        }, status=400)

    session_id = data.get('session_id')
    amount = data.get('amount')
    # Cash is the only enabled method for now; other methods
    # (M-Pesa, card) will be configured later.
    payment_method = data.get('payment_method', 'CASH').upper()

    if not session_id:
        return JsonResponse({
            'success': False,
            'message': 'session_id is required'
        }, status=400)

    if amount is None:
        return JsonResponse({
            'success': False,
            'message': 'amount is required'
        }, status=400)

    payload = {
        'session_id': session_id,
        'amount': amount,
        'payment_method': payment_method,
    }
    if data.get('transaction_reference'):
        payload['transaction_reference'] = data['transaction_reference']

    try:
        body, status = flask_client.post(f'{API_PREFIX}/payments/process', payload)
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    if body.get('success'):
        mirror.mirror_payment(session_id, body.get('data', {}))

    return JsonResponse(body, status=status)


def payment_history(request, session_id):
    """Get payment history for a parking session (local replica)."""
    session = get_object_or_404(ParkingSession, id=session_id)

    payments = session.payments.all().order_by('-payment_time')

    data = []
    for payment in payments:
        data.append({
            'payment_id': payment.id,
            'amount': float(payment.amount),
            'payment_method': payment.payment_method,
            'payment_status': payment.payment_status,
            'transaction_reference': payment.transaction_reference,
            'payment_time': payment.payment_time.isoformat(),
        })

    return JsonResponse({
        'success': True,
        'data': {
            'session_id': session.id,
            'payments': data,
            'total_paid': sum(p['amount'] for p in data),
        }
    })
