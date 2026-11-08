"""
ParkFlow Kenya - Flask API Application.

REST API (prefix /api/v1) over the parking algorithms and business rules.
Only the Django web app calls it (server-to-server); browsers never do.

Architecture:
- Routes validate input and delegate to ParkingService (services module)
- ParkingService coordinates data structures and algorithms
- Persistence via Supabase PostgreSQL (write-through _sync)

Security:
- Optional shared secret: when FLASK_API_KEY is set, every request except
  /status must send it in the X-API-Key header (Django does this).
- Unexpected exceptions are logged server-side; clients only receive a
  generic message (no stack traces / driver errors leak to the browser).
- Clients cannot choose the exit time (that would let anyone back-date an
  exit to park for free) unless PARKFLOW_ALLOW_EXIT_TIME_OVERRIDE=1 is set
  for automated end-to-end testing.
"""

import hmac
import logging
import os
import re

from flask import Blueprint, Flask, jsonify, request
from werkzeug.exceptions import BadRequest, HTTPException

from .algorithms import VALID_VEHICLE_TYPES, calculate_duration_minutes
from .config import config_by_name
from .services import (
    PersistenceError,
    get_parking_service,
    get_supabase_service,
)

logger = logging.getLogger(__name__)

# Plates are stored as at most 12 characters (DB varchar(12), incl. the
# normalising space). Allow letters/digits only - this blocks markup and
# formula characters from ever entering the database.
_PLATE_RE = re.compile(r'^[A-Z0-9]{4,11}$')


# ---------------------------------------------------------------------------
# Response / validation helpers
# ---------------------------------------------------------------------------

def ok(data=None, message=None, status=200):
    body = {'success': True}
    if message:
        body['message'] = message
    if data is not None:
        body['data'] = data
    return jsonify(body), status


def fail(message, status=400, data=None):
    body = {'success': False, 'message': message}
    if data is not None:
        body['data'] = data
    return jsonify(body), status


def _json_body() -> dict:
    """Request JSON object, or a 400 BadRequest."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise BadRequest('A JSON object body is required')
    return data


def _registration(value) -> str:
    """Validated raw registration number (normalisation happens later)."""
    if not isinstance(value, str) or not value.strip():
        raise BadRequest('Registration number is required')
    compact = ''.join(value.split()).upper()
    if not _PLATE_RE.match(compact):
        raise BadRequest(
            'Invalid registration number (letters and digits only, '
            'e.g. KAA 123A)'
        )
    return value.strip()


def _vehicle_type(value) -> str:
    vehicle_type = (value or 'CAR')
    if not isinstance(vehicle_type, str) or vehicle_type.upper() not in VALID_VEHICLE_TYPES:
        raise BadRequest(
            'Invalid vehicle type. Must be one of: ' + ', '.join(VALID_VEHICLE_TYPES)
        )
    return vehicle_type.upper()


def _int_field(data: dict, name: str, *, minimum: int = 0) -> int:
    """Required integer field (accepts 50 or "50", rejects bool/float)."""
    value = data.get(name)
    if value is None:
        raise BadRequest(f'{name} is required')
    if isinstance(value, bool):
        raise BadRequest(f'{name} must be an integer')
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        value = int(value.strip())
    if not isinstance(value, int):
        raise BadRequest(f'{name} must be an integer')
    if value < minimum:
        raise BadRequest(f'{name} must be >= {minimum}')
    return value


def _status_for(message: str, default: int) -> int:
    """Database failures are server errors; everything else is `default`."""
    return 500 if message.startswith('Database error') else default


def _barrier_message(authorized: bool) -> str:
    return 'EXIT AUTHORIZED' if authorized else 'EXIT CLOSED - Payment Required'


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(config_class=None):
    """Application factory."""
    app = Flask(__name__)
    if config_class is None:
        config_class = config_by_name(os.environ.get('FLASK_ENV', 'default'))
    app.config.from_object(config_class)

    supabase = get_supabase_service()

    # Load persisted state (source of truth = Supabase). Falls back to
    # in-memory mode with a logged warning if docs/database.sql is not
    # yet applied; the default 20-slot layout is seeded in that case.
    parking_service = get_parking_service()
    parking_service.initialize_from_supabase()

    api_key = app.config.get('API_KEY')
    if not api_key:
        logger.warning(
            'FLASK_API_KEY is not set: the engine API accepts unauthenticated '
            'requests. Set it (and the same value in Django) outside local dev.'
        )

    api = Blueprint('api', __name__, url_prefix=app.config['API_PREFIX'])

    @api.before_request
    def require_api_key():
        if not api_key or request.endpoint == 'api.api_status':
            return None
        supplied = request.headers.get('X-API-Key', '')
        if not hmac.compare_digest(supplied.encode(), api_key.encode()):
            return fail('Unauthorized', 401)
        return None

    # --- Status / read endpoints -----------------------------------------

    @api.get('/status')
    def api_status():
        return ok({
            'version': '1.1.0',
            'services': {
                'supabase': supabase.is_connected(),
                'persistence': parking_service.persistence_enabled,
            },
        }, message='ParkFlow Kenya API is running')

    @api.get('/availability')
    def api_availability():
        availability = parking_service.get_availability()
        return ok({
            'total_slots': availability.total_slots,
            'available_slots': availability.available_slots,
            'occupied_slots': availability.occupied_slots,
            'occupancy_rate': availability.occupancy_rate,
            'available_slot_numbers': availability.available_slot_numbers,
        })

    @api.get('/slots')
    def api_slots():
        """All slots: flat list, grouped-by-location grid, and stats."""
        flat = [
            {
                'slot_number': slot.slot_number,
                'status': slot.status.value,
                'location': slot.location,
            }
            for slot in parking_service.slot_registry.get_all_slots()
        ]
        return ok({
            'slots': flat,
            'locations': parking_service.get_slot_grid(),
            'stats': parking_service.slot_registry.get_stats(),
        })

    @api.get('/parking/session/<int:session_id>')
    def api_session_detail(session_id):
        session = parking_service.get_session_detail(session_id)
        if not session:
            return fail(f'Session {session_id} not found', 404)
        return ok(session)

    @api.get('/parking/sessions/active')
    def api_active_sessions():
        sessions = parking_service.get_active_sessions()
        return ok({'sessions': sessions, 'count': len(sessions)})

    @api.get('/parking/waiting-queue')
    def api_waiting_queue():
        queue = [
            dict(item, queued_at=item['queued_at'].isoformat())
            for item in parking_service.get_waiting_queue()
        ]
        return ok({'queue': queue, 'size': len(queue)})

    @api.get('/reports/daily-summary')
    def api_daily_summary():
        from .data_structures import utc_now

        availability = parking_service.get_availability()
        stats = parking_service.get_stats()
        return ok({
            'date': utc_now().date().isoformat(),
            'total_slots': availability.total_slots,
            'available_slots': availability.available_slots,
            'occupied_slots': availability.occupied_slots,
            'occupancy_rate': availability.occupancy_rate,
            'active_sessions': stats['active_sessions'],
            'waiting_queue': stats['waiting_queue'],
        })

    @api.get('/vehicles/lookup')
    def api_vehicle_lookup():
        reg_number = _registration(request.args.get('registration_number', ''))
        vehicle = parking_service.vehicle_registry.get_vehicle(reg_number)
        if not vehicle:
            return fail('Vehicle not found', 404)

        active = parking_service.session_index.get_by_vehicle(
            vehicle.registration_number
        )
        if active is not None and not active.is_active():
            active = None
        data = {
            'registration_number': vehicle.registration_number,
            'vehicle_type': vehicle.vehicle_type,
            'has_active_session': active is not None,
        }
        if active:
            data['active_session'] = {
                'session_id': active.session_id,
                'vehicle': active.vehicle_id,
                'vehicle_type': vehicle.vehicle_type,
                'slot': active.slot_id,
                'entry_time': active.entry_time.isoformat(),
                'duration_minutes': calculate_duration_minutes(active.entry_time),
                'status': active.status.value,
                'amount_due': active.amount_due,
            }
        return ok(data)

    # --- Workflow endpoints ----------------------------------------------

    @api.post('/parking/entry')
    def api_vehicle_entry():
        data = _json_body()
        result = parking_service.enter_vehicle(
            _registration(data.get('registration_number')),
            _vehicle_type(data.get('vehicle_type')),
        )

        if result.success:
            return ok({
                'session_id': result.session.session_id,
                'slot_number': result.slot.slot_number,
                'vehicle': str(result.vehicle) if result.vehicle else None,
                'entry_time': result.session.entry_time.isoformat(),
            }, message=result.message)

        if result.queued:
            # 200: the request was handled - the vehicle is now waiting.
            return fail(result.message, 200, {
                'queued': True,
                'queue_position': result.queue_position,
            })
        return fail(result.message, _status_for(result.message, 400))

    @api.post('/parking/exit')
    def api_vehicle_exit():
        data = _json_body()
        registration_number = _registration(data.get('registration_number'))

        exit_time = None
        if data.get('exit_time'):
            if not app.config['ALLOW_EXIT_TIME_OVERRIDE']:
                return fail('exit_time cannot be set by clients', 400)
            exit_time = data['exit_time']

        result = parking_service.exit_vehicle(registration_number, exit_time)
        if not result.success:
            status = 404 if result.message.startswith('No active session') else 400
            return fail(result.message, _status_for(result.message, status))

        # Barrier opens only when exit is authorized (fee 0, or paid).
        session = result.session
        vehicle = parking_service.vehicle_registry.get_vehicle(session.vehicle_id)
        return ok({
            'session_id': session.session_id,
            'vehicle': session.vehicle_id,
            'vehicle_type': vehicle.vehicle_type if vehicle else 'CAR',
            'slot_number': session.slot_id,
            'entry_time': session.entry_time.isoformat(),
            'exit_time': session.exit_time.isoformat() if session.exit_time else None,
            'duration_minutes': result.duration_minutes,
            'fee_ksh': result.fee,
            'payment_required': result.payment_required,
            'barrier_authorized': result.exit_authorized,
            'barrier_message': _barrier_message(result.exit_authorized),
        }, message=result.message)

    @api.post('/parking/fee')
    def api_calculate_fee():
        """Fee preview for a session (does not record anything)."""
        data = _json_body()
        session_id = _int_field(data, 'session_id', minimum=1)
        duration = (
            _int_field(data, 'duration_minutes')
            if data.get('duration_minutes') is not None else None
        )
        try:
            fee = parking_service.calculate_fee(session_id, duration)
        except ValueError as exc:
            return fail(str(exc), 404)
        fee['rate'] = fee['rate_used']  # backwards-compatible alias
        return ok(fee)

    @api.post('/payments/process')
    def api_process_payment():
        data = _json_body()
        session_id = _int_field(data, 'session_id', minimum=1)
        amount = _int_field(data, 'amount')
        # Cash is the only enabled method for now; M-Pesa/card later.
        payment_method = data.get('payment_method') or 'CASH'
        if not isinstance(payment_method, str):
            raise BadRequest('payment_method must be a string')
        reference = data.get('transaction_reference')
        if reference is not None and (
            not isinstance(reference, str) or len(reference) > 100
        ):
            raise BadRequest('transaction_reference must be a string of at most 100 characters')

        result = parking_service.process_payment(
            session_id, amount, payment_method, reference
        )
        if not result.success:
            status = 404 if result.message.endswith('not found') else 400
            return fail(result.message, _status_for(result.message, status))

        payment = result.payment
        return ok({
            'session_id': session_id,
            'payment_id': payment.payment_id,
            'transaction_reference': payment.transaction_reference,
            'amount': payment.amount,
            'payment_method': payment.payment_method,
            'payment_time': payment.payment_time.isoformat(),
            'exit_authorized': result.exit_authorized,
            'barrier_message': _barrier_message(result.exit_authorized),
        }, message=result.message)

    @api.post('/vehicles/register')
    def api_vehicle_register():
        """Register a vehicle (master data) without parking it."""
        data = _json_body()
        registration_number = _registration(data.get('registration_number'))
        vehicle_type = _vehicle_type(data.get('vehicle_type'))

        try:
            vehicle = parking_service.register_vehicle(registration_number, vehicle_type)
        except PersistenceError:
            logger.exception('Vehicle registration persistence failed')
            return fail('Database error recording vehicle. Check the engine log.', 500)

        if vehicle is None:
            from .data_structures import normalize_registration
            return fail('Vehicle already registered', 400, {
                'registration_number': normalize_registration(registration_number),
            })
        return ok({
            'registration_number': vehicle.registration_number,
            'vehicle_type': vehicle.vehicle_type,
        }, message='Vehicle registered successfully')

    app.register_blueprint(api)

    # --- Error handling (JSON everywhere, no internals leaked) -----------

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        return fail(exc.description or exc.name, exc.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected_error(exc):
        logger.exception('Unhandled error on %s %s', request.method, request.path)
        return fail('Internal server error', 500)

    return app
