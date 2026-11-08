"""
Payments app views.

Fee calculation and payment processing are proxied to the Flask
parking engine (Browser -> Django -> Flask -> Supabase); successful
payments are mirrored into the local reporting database. The list,
receipt and history pages read the local replica.
"""
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET, require_POST

from common import flask_client, mirror
from common.flask_client import API_PREFIX, FlaskAPIError
from common.http import BadInput, error, json_body, text_field
from parking.models import ParkingSession

from .models import Payment

PAGE_SIZE = 50


def _positive_int(value, name):
    """Parse a positive integer from a query/JSON value (400 on failure)."""
    if isinstance(value, bool):
        raise BadInput(f'{name} must be a positive integer')
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise BadInput(f'{name} must be a positive integer')
    if number < 1:
        raise BadInput(f'{name} must be a positive integer')
    return number


@require_GET
def payment_list(request):
    """All payments (local reporting replica), newest first, paginated."""
    payments = Payment.objects.select_related(
        'session__vehicle', 'session__slot'
    ).order_by('-payment_time')
    page = Paginator(payments, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'payments/list.html', {
        'payments': page,
        'page_obj': page,
    })


@require_GET
def payment_receipt(request, payment_id):
    """Printable receipt for a payment (local replica)."""
    payment = get_object_or_404(
        Payment.objects.select_related('session__vehicle', 'session__slot'),
        id=payment_id,
    )
    return render(request, 'payments/receipt.html', {'payment': payment})


@require_GET
def calculate_fee(request):
    """
    Fee preview for a session via the engine.

    Query parameters:
    - session_id: ID of the parking session (required)
    - duration_minutes: optional duration override
    """
    try:
        payload = {'session_id': _positive_int(request.GET.get('session_id'), 'session_id')}
        duration = request.GET.get('duration_minutes')
        if duration:
            try:
                payload['duration_minutes'] = int(duration)
            except ValueError:
                raise BadInput('duration_minutes must be an integer')
            if payload['duration_minutes'] < 0:
                raise BadInput('duration_minutes must be >= 0')
    except BadInput as exc:
        return error(str(exc))

    try:
        body, status = flask_client.post(f'{API_PREFIX}/parking/fee', payload)
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)
    return JsonResponse(body, status=status)


@require_POST
def process_payment(request):
    """
    Record a cash payment via the engine, then mirror it locally.

    Expected POST data: {"session_id": 1, "amount": 50, "payment_method": "CASH"}

    The transaction reference is always generated server-side for cash.
    """
    try:
        data = json_body(request)
        session_id = _positive_int(data.get('session_id'), 'session_id')
        if data.get('amount') is None:
            raise BadInput('amount is required')
        payment_method = text_field(
            data, 'payment_method', required=False, default='CASH', max_length=20
        ).upper()
    except BadInput as exc:
        return error(str(exc))

    try:
        body, status = flask_client.post(f'{API_PREFIX}/payments/process', {
            'session_id': session_id,
            'amount': data['amount'],  # validated by the engine
            'payment_method': payment_method,
        })
    except FlaskAPIError as exc:
        body, status = flask_client.unavailable(exc)

    if body.get('success'):
        result = body.setdefault('data', {})
        mirror.mirror_payment(session_id, result)
        # Point the browser at the printable receipt for this payment.
        reference = result.get('transaction_reference')
        payment = (
            Payment.objects.filter(transaction_reference=reference).first()
            if reference else None
        )
        if payment:
            result['receipt_url'] = payment.get_absolute_url()
        messages.success(request, body.get('message') or 'Payment processed')

    return JsonResponse(body, status=status)


@require_GET
def payment_history(request, session_id):
    """Payment history for a parking session (local replica)."""
    session = get_object_or_404(ParkingSession, id=session_id)
    payments = session.payments.order_by('-payment_time')

    data = [
        {
            'payment_id': payment.id,
            'amount': float(payment.amount),
            'payment_method': payment.payment_method,
            'payment_status': payment.payment_status,
            'transaction_reference': payment.transaction_reference,
            'payment_time': payment.payment_time.isoformat(),
        }
        for payment in payments
    ]
    total_paid = payments.filter(
        payment_status=Payment.PaymentStatus.PAID
    ).aggregate(total=Sum('amount'))['total'] or 0

    return JsonResponse({
        'success': True,
        'data': {
            'session_id': session.id,
            'payments': data,
            # Only settled payments count (refunded/failed ones do not).
            'total_paid': float(total_paid),
        },
    })
