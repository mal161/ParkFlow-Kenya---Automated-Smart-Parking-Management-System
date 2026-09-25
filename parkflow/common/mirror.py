"""
Local reporting replica (Django ORM) of workflow events.

Flask + Supabase own the transactional truth. After a successful
workflow call the Django proxy mirrors the result into its local
SQLite database, which the list pages and management reports read.

Mirror writes are best-effort: if one fails, the user flow still
succeeds (the engine already recorded it) and the failure is logged.

Used by the proxied views:
- mirror_entry / mirror_exit / mirror_payment: workflow results
- sync_slots / sync_sessions: refresh helpers so queue-served sessions
  and admin slot edits also appear locally.
"""

import logging
from datetime import datetime, timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _aware(iso_value):
    """Parse an ISO-8601 string (naive = UTC, as produced by Flask)."""
    if not iso_value:
        return timezone.now()
    if isinstance(iso_value, datetime):
        dt = iso_value
    else:
        dt = datetime.fromisoformat(str(iso_value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def upsert_active_session(session_id, registration_number, vehicle_type,
                           slot_number, entry_time):
    """Create or refresh the local copy of an ACTIVE session."""
    from vehicles.models import Vehicle
    from parking.models import ParkingSlot, ParkingSession

    vehicle, _ = Vehicle.objects.get_or_create(
        registration_number=registration_number,
        defaults={'vehicle_type': vehicle_type},
    )
    slot, _ = ParkingSlot.objects.get_or_create(
        slot_number=slot_number,
        defaults={'location': ''},
    )
    session = ParkingSession.objects.filter(id=session_id).first()
    if session is None:
        ParkingSession.objects.create(
            id=session_id,
            vehicle=vehicle,
            slot=slot,
            entry_time=_aware(entry_time),
            status=ParkingSession.Status.ACTIVE,
        )
    elif session.status == ParkingSession.Status.ACTIVE:
        session.entry_time = _aware(entry_time)
        session.save(update_fields=['entry_time', 'updated_at'])
    return session


def mirror_entry(registration_number, vehicle_type, data):
    """Record a successful vehicle entry in the local database."""
    try:
        with transaction.atomic():
            upsert_active_session(
                data['session_id'],
                registration_number,
                vehicle_type,
                data['slot_number'],
                data.get('entry_time'),
            )
    except Exception as exc:
        logger.warning('Entry mirror failed: %s', exc)


def mirror_exit(data):
    """
    Record exit details for a session.

    Fee 0 exits complete the session immediately (the engine released
    the slot already); fee > 0 exits keep it ACTIVE until payment.
    """
    try:
        from parking.models import ParkingSession

        with transaction.atomic():
            session = ParkingSession.objects.filter(id=data['session_id']).first()
            if session is None or session.exit_time is not None:
                return
            session.exit_time = _aware(data.get('exit_time'))
            session.duration_minutes = data.get('duration_minutes', 0)
            session.amount_due = data.get('fee_ksh', 0)
            if data.get('fee_ksh') == 0 and data.get('barrier_authorized'):
                session.status = ParkingSession.Status.COMPLETED
            session.save()
    except Exception as exc:
        logger.warning('Exit mirror failed: %s', exc)


def mirror_payment(session_id, data):
    """Record the cash payment and complete the local session copy."""
    try:
        from parking.models import ParkingSession
        from payments.models import Payment

        with transaction.atomic():
            session = ParkingSession.objects.filter(id=session_id).first()
            if session is None:
                return
            reference = data.get('transaction_reference')
            if reference and not Payment.objects.filter(
                transaction_reference=reference
            ).exists():
                Payment.objects.create(
                    session=session,
                    amount=data.get('amount', 0),
                    payment_method=Payment.PaymentMethod.CASH,
                    payment_status=Payment.PaymentStatus.PAID,
                    transaction_reference=reference,
                    payment_time=_aware(data.get('payment_time')),
                )
            session.status = ParkingSession.Status.COMPLETED
            session.amount_due = data.get('amount', session.amount_due)
            session.save()
    except Exception as exc:
        logger.warning('Payment mirror failed: %s', exc)


def mirror_vehicle(registration_number, vehicle_type):
    """Record a registered vehicle in the local database."""
    try:
        from vehicles.models import Vehicle

        Vehicle.objects.get_or_create(
            registration_number=registration_number,
            defaults={'vehicle_type': vehicle_type},
        )
    except Exception as exc:
        logger.warning('Vehicle mirror failed: %s', exc)


def sync_slots():
    """Pull the engine's slot list into the local database."""
    from . import flask_client
    from parking.models import ParkingSlot

    try:
        body, _ = flask_client.get(f'{flask_client.API_PREFIX}/slots')
    except flask_client.FlaskAPIError as exc:
        logger.warning('Slot sync skipped: %s', exc)
        return False
    if not body.get('success'):
        return False
    try:
        with transaction.atomic():
            for slot in body['data']['slots']:
                obj, created = ParkingSlot.objects.get_or_create(
                    slot_number=slot['slot_number'],
                    defaults={'location': slot.get('location', '')},
                )
                if created:
                    continue
                updates = []
                location = slot.get('location', '')
                if location and obj.location != location:
                    obj.location = location
                    updates.append('location')
                if obj.status != slot['status']:
                    obj.status = slot['status']
                    updates.append('status')
                if updates:
                    obj.save(update_fields=updates + ['updated_at'])
        return True
    except Exception as exc:
        logger.warning('Slot sync failed: %s', exc)
        return False


def sync_sessions():
    """Pull the engine's active sessions into the local database."""
    from . import flask_client

    try:
        body, _ = flask_client.get(
            f'{flask_client.API_PREFIX}/parking/sessions/active'
        )
    except flask_client.FlaskAPIError as exc:
        logger.warning('Session sync skipped: %s', exc)
        return False
    if not body.get('success'):
        return False
    try:
        with transaction.atomic():
            for session in body['data']['sessions']:
                upsert_active_session(
                    session['session_id'],
                    session['vehicle']['registration_number'],
                    session['vehicle'].get('vehicle_type', 'CAR'),
                    session['slot']['slot_number'],
                    session['entry_time'],
                )
        return True
    except Exception as exc:
        logger.warning('Session sync failed: %s', exc)
        return False
