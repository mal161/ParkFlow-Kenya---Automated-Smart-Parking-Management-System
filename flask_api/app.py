"""
ParkFlow Kenya - Flask API Application.

Flask API service for parking algorithms and business logic.
Provides REST endpoints for Django to consume.

Architecture:
- Flask blueprint routes delegate to services module
- Services use data structures and algorithms
- Persistence via Supabase PostgreSQL
"""

import os
from flask import Flask, request, jsonify, Blueprint
from flask_cors import CORS

from .config import Config, DevelopmentConfig, ProductionConfig, TestingConfig
from .services import (
    get_parking_service,
    init_parking_service,
    get_supabase_service,
    SupabaseService,
)
from .algorithms import (
    calculate_parking_fee,
    calculate_duration_minutes,
    check_availability,
    authorize_exit_barrier,
)


def create_app(config_class=DevelopmentConfig):
    """Application factory pattern."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # CORS restricted to the Django dev server: browsers never call the
    # Flask API directly (Django proxies server-to-server), so only the
    # local Django origin needs to be allowed.
    CORS(app, resources={r"/api/*": {"origins": [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]}})

    # Supabase connection (shared singleton)
    supabase = get_supabase_service()

    # Load persisted state (source of truth = Supabase). Falls back to
    # in-memory mode with a logged warning if docs/database.sql is not
    # yet applied; the default 20-slot layout is seeded in that case.
    parking_service = get_parking_service()
    parking_service.initialize_from_supabase()

    # Register blueprint
    api_blueprint = Blueprint('api', __name__, url_prefix='/api/v1')

    # --- API Routes ---

    @api_blueprint.route('/status', methods=['GET'])
    def api_status():
        """API status check."""
        return jsonify({
            'success': True,
            'message': 'ParkFlow Kenya API is running',
            'version': '1.0.0',
            'services': {
                'supabase': supabase.is_connected(),
                'parking': True,
            }
        })


    @api_blueprint.route('/availability', methods=['GET'])
    def api_availability():
        """Get current parking slot availability."""
        try:
            availability = parking_service.get_availability()

            return jsonify({
                'success': True,
                'data': {
                    'total_slots': availability.total_slots,
                    'available_slots': availability.available_slots,
                    'occupied_slots': availability.occupied_slots,
                    'occupancy_rate': availability.occupancy_rate,
                    'available_slot_numbers': availability.available_slot_numbers,
                }
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'message': str(e)
            }), 500


    @api_blueprint.route('/parking/entry', methods=['POST'])
    def api_vehicle_entry():
        """Process vehicle entry."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'message': 'JSON data is required'
                }), 400

            registration_number = data.get('registration_number', '').strip()
            vehicle_type = data.get('vehicle_type', 'CAR').upper()

            if not registration_number:
                return jsonify({
                    'success': False,
                    'message': 'Registration number is required'
                }), 400

            result = parking_service.enter_vehicle(registration_number, vehicle_type)

            if result.success:
                return jsonify({
                    'success': True,
                    'message': result.message,
                    'data': {
                        'session_id': result.session.session_id if result.session else None,
                        'slot_number': result.slot.slot_number if result.slot else None,
                        'vehicle': str(result.vehicle) if result.vehicle else None,
                        'entry_time': result.session.entry_time.isoformat() if result.session else None,
                    }
                })
            else:
                # Handle waiting queue
                if result.queued:
                    return jsonify({
                        'success': False,
                        'message': result.message,
                        'data': {
                            'queued': True,
                            'queue_position': result.queue_position,
                        }
                    }), 200
                else:
                    status = 500 if result.message.startswith('Database error') else 400
                    return jsonify({
                        'success': False,
                        'message': result.message,
                    }), status

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/parking/exit', methods=['POST'])
    def api_vehicle_exit():
        """Process vehicle exit."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'message': 'JSON data is required'
                }), 400

            registration_number = data.get('registration_number', '').strip()

            if not registration_number:
                return jsonify({
                    'success': False,
                    'message': 'Registration number is required'
                }), 400

            exit_time = data.get('exit_time')

            result = parking_service.exit_vehicle(registration_number, exit_time)

            if result.success:
                # Barrier opens only when exit is authorized
                # (fee 0, or payment already recorded).
                session = result.session
                vehicle = parking_service.vehicle_registry.get_vehicle(
                    session.vehicle_id
                ) if session else None
                return jsonify({
                    'success': True,
                    'message': result.message,
                    'data': {
                        'session_id': session.session_id if session else None,
                        'vehicle': session.vehicle_id if session else registration_number,
                        'vehicle_type': vehicle.vehicle_type if vehicle else 'CAR',
                        'slot_number': result.slot.slot_number if result.slot else (session.slot_id if session else None),
                        'entry_time': session.entry_time.isoformat() if session else None,
                        'exit_time': session.exit_time.isoformat() if session and session.exit_time else None,
                        'duration_minutes': result.duration_minutes,
                        'fee_ksh': result.fee,
                        'payment_required': result.payment_required,
                        'barrier_authorized': result.exit_authorized,
                        'barrier_message': 'EXIT AUTHORIZED' if result.exit_authorized else 'EXIT CLOSED - Payment Required',
                    }
                })
            else:
                status = 500 if result.message.startswith('Database error') else 404
                return jsonify({
                    'success': False,
                    'message': result.message,
                }), status

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/parking/fee', methods=['POST'])
    def api_calculate_fee():
        """Calculate parking fee."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'message': 'JSON data is required'
                }), 400

            session_id = data.get('session_id')
            duration_minutes = data.get('duration_minutes')

            if not session_id:
                return jsonify({
                    'success': False,
                    'message': 'session_id is required'
                }), 400

            # Get session from service
            session = parking_service.session_index.get_session(session_id)
            if not session:
                return jsonify({
                    'success': False,
                    'message': f'Session {session_id} not found'
                }), 404

            # Calculate duration
            if duration_minutes is not None:
                duration = duration_minutes
            else:
                duration = calculate_duration_minutes(session.entry_time, session.exit_time)

            # Calculate fee
            vehicle = parking_service.vehicle_registry.get_vehicle(session.vehicle_id)
            vehicle_type = vehicle.vehicle_type if vehicle else 'CAR'
            fee = calculate_parking_fee(vehicle_type, duration)

            from flask_api.algorithms import DEFAULT_FEE_STRUCTURES
            fee_structure = DEFAULT_FEE_STRUCTURES.get(vehicle_type, DEFAULT_FEE_STRUCTURES['CAR'])

            return jsonify({
                'success': True,
                'data': {
                    'session_id': session_id,
                    'vehicle': f'{session.vehicle_id} ({vehicle_type})',
                    'vehicle_type': vehicle_type,
                    'duration_minutes': duration,
                    'fee_ksh': fee,
                    'rate': {
                        'free_minutes': fee_structure.free_minutes,
                        'two_hour_rate': fee_structure.two_hour_rate,
                        'four_hour_rate': fee_structure.four_hour_rate,
                        'six_hour_rate': fee_structure.six_hour_rate,
                        'over_six_hour_rate': fee_structure.over_six_hour_rate,
                    },
                    'rate_used': {
                        'free_minutes': fee_structure.free_minutes,
                        'two_hour_rate': fee_structure.two_hour_rate,
                        'four_hour_rate': fee_structure.four_hour_rate,
                        'six_hour_rate': fee_structure.six_hour_rate,
                        'over_six_hour_rate': fee_structure.over_six_hour_rate,
                    },
                }
            })

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/parking/session/<int:session_id>', methods=['GET'])
    def api_session_detail(session_id):
        """Get parking session details."""
        try:
            session = parking_service.get_session_detail(session_id)
            if not session:
                return jsonify({
                    'success': False,
                    'message': f'Session {session_id} not found'
                }), 404

            return jsonify({
                'success': True,
                'data': session
            })

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/parking/sessions/active', methods=['GET'])
    def api_active_sessions():
        """Get all active parking sessions."""
        try:
            sessions = parking_service.get_active_sessions()

            return jsonify({
                'success': True,
                'data': {
                    'sessions': sessions,
                    'count': len(sessions),
                }
            })

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/parking/waiting-queue', methods=['GET'])
    def api_waiting_queue():
        """Get current waiting queue."""
        try:
            queue = parking_service.get_waiting_queue()
            return jsonify({
                'success': True,
                'data': {
                    'queue': queue,
                    'size': parking_service.waiting_queue.size(),
                }
            })

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/payments/process', methods=['POST'])
    def api_process_payment():
        """Process payment for a session."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'message': 'JSON data is required'
                }), 400

            session_id = data.get('session_id')
            amount = data.get('amount')
            # Cash is the only enabled method for now; other methods
            # (M-Pesa, card) will be configured later.
            payment_method = data.get('payment_method', 'CASH')
            transaction_reference = data.get('transaction_reference')

            if not session_id:
                return jsonify({
                    'success': False,
                    'message': 'session_id is required'
                }), 400

            if amount is None:
                return jsonify({
                    'success': False,
                    'message': 'amount is required'
                }), 400

            result = parking_service.process_payment(
                session_id, amount, payment_method, transaction_reference
            )

            if result.success:
                return jsonify({
                    'success': True,
                    'message': result.message,
                    'data': {
                        'session_id': session_id,
                        'payment_id': result.payment.payment_id if result.payment else None,
                        'transaction_reference': result.payment.transaction_reference if result.payment else None,
                        'amount': result.payment.amount if result.payment else None,
                        'payment_method': result.payment.payment_method if result.payment else None,
                        'payment_time': result.payment.payment_time.isoformat() if result.payment else None,
                        'exit_authorized': result.exit_authorized,
                        'barrier_message': 'EXIT AUTHORIZED' if result.exit_authorized else 'EXIT CLOSED - Payment Required',
                    }
                })
            else:
                status = 500 if result.message.startswith('Database error') else 400
                return jsonify({
                    'success': False,
                    'message': result.message,
                }), status

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/payments/history/<int:session_id>', methods=['GET'])
    def api_payment_history(session_id):
        """Get payment history for a session."""
        try:
            # Check if session exists
            session = parking_service.session_index.get_session(session_id)
            if not session:
                return jsonify({
                    'success': False,
                    'message': f'Session {session_id} not found'
                }), 404

            # Get payments - in production would query Supabase
            return jsonify({
                'success': True,
                'data': {
                    'session_id': session_id,
                    'vehicle': session.vehicle_id,
                    'slot': session.slot_id,
                    'total_paid': session.amount_due,
                }
            })

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/vehicles/lookup', methods=['GET'])
    def api_vehicle_lookup():
        """Look up vehicle by registration number."""
        try:
            reg_number = request.args.get('registration_number', '').strip()

            if not reg_number:
                return jsonify({
                    'success': False,
                    'message': 'Registration number is required'
                }), 400

            result = parking_service.vehicle_registry.get_vehicle(reg_number)

            if result:
                active = parking_service.session_index.get_by_vehicle(
                    result.registration_number
                )
                data = {
                    'registration_number': result.registration_number,
                    'vehicle_type': result.vehicle_type,
                    'has_active_session': active is not None,
                }
                if active:
                    data['active_session'] = {
                        'session_id': active.session_id,
                        'vehicle': active.vehicle_id,
                        'vehicle_type': result.vehicle_type,
                        'slot': active.slot_id,
                        'entry_time': active.entry_time.isoformat(),
                        'duration_minutes': calculate_duration_minutes(
                            active.entry_time
                        ),
                        'status': active.status.value,
                        'amount_due': active.amount_due,
                    }
                return jsonify({
                    'success': True,
                    'data': data
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Vehicle not found'
                }), 404

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/reports/daily-summary', methods=['GET'])
    def api_daily_summary():
        """Get daily summary report."""
        try:
            from datetime import datetime, timedelta, timezone

            end_date = datetime.now(timezone.utc)
            start_date = end_date - timedelta(days=1)

            availability = parking_service.get_availability()
            stats = parking_service.get_stats()

            return jsonify({
                'success': True,
                'data': {
                    'date': end_date.date().isoformat(),
                    'total_slots': availability.total_slots,
                    'available_slots': availability.available_slots,
                    'occupied_slots': availability.occupied_slots,
                    'occupancy_rate': availability.occupancy_rate,
                    'active_sessions': stats['active_sessions'],
                    'waiting_queue': stats['waiting_queue'],
                }
            })

        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/slots', methods=['GET'])
    def api_slots():
        """All slots: flat list, grouped-by-location grid, and stats."""
        try:
            slots = parking_service.slot_registry.get_all_slots()
            flat = [
                {
                    'slot_number': slot.slot_number,
                    'status': slot.status.value,
                    'location': slot.location,
                }
                for slot in slots
            ]
            return jsonify({
                'success': True,
                'data': {
                    'slots': flat,
                    'locations': parking_service.get_slot_grid(),
                    'stats': parking_service.slot_registry.get_stats(),
                }
            })
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    @api_blueprint.route('/vehicles/register', methods=['POST'])
    def api_vehicle_register():
        """Register a vehicle (master data) without parking it."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'message': 'JSON data is required'
                }), 400

            registration_number = data.get('registration_number', '').strip()
            vehicle_type = data.get('vehicle_type', 'CAR').upper()

            if not registration_number:
                return jsonify({
                    'success': False,
                    'message': 'Registration number is required'
                }), 400

            valid_types = ['CAR', 'MOTORCYCLE', 'TRUCK', 'BUS', 'OTHER']
            if vehicle_type not in valid_types:
                return jsonify({
                    'success': False,
                    'message': f'Invalid vehicle type. Must be one of: {", ".join(valid_types)}'
                }), 400

            from flask_api.data_structures import Vehicle, VehicleRegistry
            norm_reg = VehicleRegistry.normalize_registration(registration_number)

            # Persist first: if the write fails nothing changes in memory.
            if parking_service.persistence_enabled:
                get_supabase_service().get_or_create_vehicle(norm_reg, vehicle_type)

            created = parking_service.vehicle_registry.register_vehicle(
                Vehicle(registration_number=norm_reg, vehicle_type=vehicle_type)
            )

            if not created:
                return jsonify({
                    'success': False,
                    'message': 'Vehicle already registered',
                    'data': {'registration_number': norm_reg}
                }), 400

            return jsonify({
                'success': True,
                'message': 'Vehicle registered successfully',
                'data': {
                    'registration_number': norm_reg,
                    'vehicle_type': vehicle_type,
                }
            })

        except Exception as e:
            from flask_api.services import PersistenceError, _DB_ERRORS
            if isinstance(e, PersistenceError) or isinstance(e, _DB_ERRORS):
                return jsonify({
                    'success': False,
                    'message': f'Database error recording vehicle: {e}'
                }), 500
            return jsonify({
                'success': False,
                'message': f'Server error: {str(e)}'
            }), 500


    # Register blueprint with app
    app.register_blueprint(api_blueprint)

    # Initialize parking service with data if provided
    if parking_service:
        # Service already initialized at module level
        pass

    @app.teardown_appcontext
    def teardown(exception):
        pass

    return app


def init_flask_api(slots_data=None, vehicles_data=None, sessions_data=None):
    """Initialize the Flask API with database data."""
    app = create_app(DevelopmentConfig)

    if slots_data is not None and vehicles_data is not None and sessions_data is not None:
        init_parking_service(slots_data, vehicles_data, sessions_data)

    return app