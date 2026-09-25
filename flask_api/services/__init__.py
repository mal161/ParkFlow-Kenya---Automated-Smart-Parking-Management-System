"""
ParkFlow Kenya - Services Module.

Business logic services that coordinate data structures and algorithms.
These services are used by the Flask API routes.

Persistence model:
- Supabase (PostgreSQL) is the source of truth.
- In-memory data structures (registries, indexes, queue) hold the working
  state for O(1) processing; _sync() writes every change through to
  Supabase (diff-based: only rows that changed are written).
- If the database is unreachable or docs/database.sql has not been run
  yet, the service runs in-memory only and logs a clear startup warning.
"""

import os
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from dotenv import load_dotenv

from flask_api.data_structures import (
    ParkingSlot,
    Vehicle,
    ParkingSession,
    Payment,
    ParkingSlotRegistry,
    VehicleRegistry,
    WaitingQueue,
    SessionIndex,
    SlotStatus,
    SessionStatus,
    PaymentStatus,
)
from flask_api.algorithms import (
    FeeStructure,
    DEFAULT_FEE_STRUCTURES,
    calculate_parking_fee,
    calculate_duration_minutes,
    FirstFitAllocation,
    process_vehicle_entry,
    process_vehicle_exit,
    process_payment,
    check_availability,
    authorize_exit_barrier,
    EntryResult,
    ExitResult,
    PaymentResult,
    AvailabilityInfo,
    BarrierState,
)

logger = logging.getLogger(__name__)

# Driver exceptions treated as persistence failures. Kept precise so real
# programming errors still surface as unexpected exceptions in development.
_DB_ERRORS: tuple = ()
try:
    from postgrest.exceptions import APIError as _PostgrestAPIError
    _DB_ERRORS += (_PostgrestAPIError,)
except ImportError:  # pragma: no cover
    pass
try:
    import httpx
    _DB_ERRORS += (httpx.HTTPError,)
except ImportError:  # pragma: no cover
    pass
if not _DB_ERRORS:  # pragma: no cover
    _DB_ERRORS = (Exception,)


class PersistenceError(Exception):
    """A Supabase write failed. Callers roll back in-memory state."""


def _iso_utc(dt: datetime) -> str:
    """Format a datetime as ISO-8601 UTC for Supabase timestamptz columns."""
    if dt.tzinfo is None:
        return dt.isoformat() + 'Z'
    return dt.isoformat()


class ParkingService:
    """
    Core parking management service.

    Coordinates slot registry, vehicle registry, session index, and waiting
    queue. Every mutation that must survive a restart is written through
    to Supabase via _sync(); failures raise PersistenceError so callers can
    roll the in-memory state back and report the error to the client.
    """

    def __init__(self):
        self.slot_registry = ParkingSlotRegistry()
        self.vehicle_registry = VehicleRegistry()
        self.session_index = SessionIndex()
        self.waiting_queue = WaitingQueue()
        self.allocation_strategy = FirstFitAllocation()
        self.persistence_enabled = False
        self._initialized = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------
    def initialize_from_database(self, slots_data: List[Dict], vehicles_data: List[Dict], sessions_data: List[Dict]) -> None:
        """
        Initialize service state from database records.

        Args:
            slots_data: List of slot dictionaries from database
            vehicles_data: List of vehicle dictionaries from database
            sessions_data: List of active session dictionaries from database
        """
        for slot_data in slots_data:
            slot = ParkingSlot.from_dict(slot_data)
            self.slot_registry.add_slot(slot)

        for vehicle_data in vehicles_data:
            vehicle = Vehicle.from_dict(vehicle_data)
            self.vehicle_registry.register_vehicle(vehicle)

        for session_data in sessions_data:
            session = ParkingSession.from_dict(session_data)
            if session.is_active():
                self.session_index.add_session(session)
                slot = self.slot_registry.get_slot(session.slot_id)
                if slot:
                    slot.occupy()

        self._initialized = True

    def initialize_from_supabase(self) -> bool:
        """
        Load persisted state from Supabase (source of truth).

        Returns True when persistence is active. When the schema is not
        ready (docs/database.sql not yet run) or the network fails, logs a
        warning and returns False - the service then runs in-memory only,
        seeding the default slot layout if no slots exist yet.
        """
        sb = get_supabase_service()
        if not sb.is_connected():
            logger.warning(
                "Supabase client not configured (check SUPABASE_URL/"
                "SUPABASE_KEY in .env). Running in-memory only."
            )
            self._seed_default_slots_if_empty()
            return False

        try:
            slot_rows = sb.load_slots()
            vehicle_rows = sb.load_vehicles()
            session_rows = sb.load_active_sessions()
            all_session_ids = sb.load_all_session_ids()
        except Exception as exc:  # startup fallback: full error is logged
            logger.warning(
                "Supabase schema not ready (%s). Run docs/database.sql in "
                "the Supabase SQL editor, then restart. Running in-memory "
                "only.", exc,
            )
            self._seed_default_slots_if_empty()
            return False

        slot_id_to_number = {row['id']: row['slot_number'] for row in slot_rows}
        vehicle_id_to_reg = {row['id']: row['registration_number'] for row in vehicle_rows}

        session_dicts = []
        for row in session_rows:
            reg = vehicle_id_to_reg.get(row['vehicle_id'])
            number = slot_id_to_number.get(row['slot_id'])
            if not reg or not number:
                logger.warning(
                    "Skipping session %s: missing vehicle/slot reference.",
                    row['id'],
                )
                continue
            session_dicts.append({
                'session_id': row['id'],
                'vehicle_id': reg,
                'slot_id': number,
                'entry_time': row['entry_time'],
                'exit_time': row.get('exit_time'),
                'duration_minutes': row.get('duration_minutes', 0),
                'amount_due': int(float(row.get('amount_due', 0) or 0)),
                'status': row.get('status', 'ACTIVE'),
            })

        self.initialize_from_database(slot_rows, vehicle_rows, session_dicts)

        # Mark loaded state as already synced so _sync() only writes changes.
        for slot in self.slot_registry.get_all_slots():
            slot._synced_status = slot.status.value
        for session in self.session_index.get_all_sessions():
            session.db_id = session.session_id
            session._synced_exit_time = session.exit_time
            session._synced_status = session.status
        # New ids must not collide with completed sessions (not loaded).
        self.session_index.id_floor = max(all_session_ids, default=0)

        self.persistence_enabled = True
        logger.info(
            "Loaded %d slots, %d vehicles, %d active sessions from Supabase.",
            len(slot_rows), len(vehicle_rows), len(session_dicts),
        )
        return True

    def _seed_default_slots_if_empty(self) -> None:
        """Create the default 20-slot layout when no slots exist at all."""
        if len(self.slot_registry) > 0:
            return
        for i in range(1, 16):
            self.slot_registry.add_slot(
                ParkingSlot(slot_number=f"A{i:02d}", location="Block A - Main")
            )
        for i in range(1, 6):
            self.slot_registry.add_slot(
                ParkingSlot(slot_number=f"B{i:02d}", location="Block B - Overflow")
            )
        logger.info("Seeded default 20-slot layout in memory.")

    # ------------------------------------------------------------------
    # Persistence (write-through sync)
    # ------------------------------------------------------------------
    def _sync(self) -> None:
        """
        Write pending in-memory changes to Supabase (diff-based).

        - Sessions without a database id are inserted (vehicle/slot FKs
          resolved to database ids first)
        - Sessions whose exit fields or status changed are updated
        - Slots whose status changed are updated

        Raises PersistenceError when a write fails so callers can roll back.
        """
        if not self.persistence_enabled:
            return
        sb = get_supabase_service()
        try:
            for session in self.session_index.get_all_sessions():
                if getattr(session, 'db_id', None) is None:
                    vehicle_row = sb.get_or_create_vehicle(
                        session.vehicle_id,
                        self._vehicle_type(session.vehicle_id),
                    )
                    slot_row = sb.get_slot_by_number(session.slot_id)
                    if not slot_row:
                        slot = self.slot_registry.get_slot(session.slot_id)
                        slot_row = sb.create_slot(
                            session.slot_id, slot.location if slot else ""
                        )
                    row = sb.insert_session(
                        vehicle_row['id'], slot_row['id'], session.entry_time
                    )
                    # Re-key the session to the database id so API ids and
                    # database ids are identical (index + object).
                    old_id = session.session_id
                    session.session_id = row['id']
                    session.db_id = row['id']
                    self.session_index.remove_session(old_id)
                    self.session_index.add_session(session)
                    self.session_index.id_floor = max(
                        self.session_index.id_floor, session.session_id
                    )
                    session._synced_exit_time = session.exit_time
                    session._synced_status = session.status
                elif (
                    session.exit_time != getattr(session, '_synced_exit_time', None)
                    or session.status != getattr(session, '_synced_status', None)
                ):
                    sb.update_session(
                        session.db_id,
                        exit_time=session.exit_time,
                        duration_minutes=session.duration_minutes,
                        amount_due=session.amount_due,
                        status=session.status.value,
                    )
                    session._synced_exit_time = session.exit_time
                    session._synced_status = session.status

            for slot in self.slot_registry.get_all_slots():
                if getattr(slot, '_synced_status', None) != slot.status.value:
                    if not sb.update_slot_status(slot.slot_number, slot.status.value):
                        raise PersistenceError(
                            f"Slot {slot.slot_number} not found in database"
                        )
                    slot._synced_status = slot.status.value
        except PersistenceError:
            raise
        except _DB_ERRORS as exc:
            raise PersistenceError(str(exc)) from exc

    def _vehicle_type(self, registration_number: str) -> str:
        """Vehicle type for FK creation (defaults to CAR if unknown)."""
        vehicle = self.vehicle_registry.get_vehicle(registration_number)
        return vehicle.vehicle_type if vehicle else "CAR"

    def _serve_waiting_queue(self) -> str:
        """
        Allocate the just-freed slot to the next FIFO vehicle.

        Each allocation persists itself via enter_vehicle(); if the write
        fails, that vehicle's allocation is rolled back and it stays queued.

        Returns:
            Human-readable message ('' when the queue was empty).
        """
        next_vehicle = self.waiting_queue.dequeue()
        if not next_vehicle:
            return ""

        result = self.enter_vehicle(
            next_vehicle['registration_number'],
            next_vehicle['vehicle_type'],
        )
        if result.success:
            return (
                f" Waiting vehicle {next_vehicle['registration_number']} "
                f"allocated slot {result.slot.slot_number}."
            )

        # Could not allocate (or persistence failed) - keep it queued.
        self.waiting_queue.enqueue(
            next_vehicle['registration_number'], next_vehicle['vehicle_type']
        )
        return " Parking still full."

    # ------------------------------------------------------------------
    # Workflow operations
    # ------------------------------------------------------------------
    def get_availability(self) -> AvailabilityInfo:
        """Get current parking availability."""
        return check_availability(self.slot_registry)

    def enter_vehicle(self, registration_number: str, vehicle_type: str = "CAR") -> EntryResult:
        """
        Process vehicle entry and persist the new session.

        Queued and duplicate outcomes create no persistent state (the
        waiting queue itself is transient in-memory processing state).

        Args:
            registration_number: Vehicle registration
            vehicle_type: Type of vehicle

        Returns:
            EntryResult with outcome
        """
        result = process_vehicle_entry(
            self.slot_registry,
            self.vehicle_registry,
            self.session_index,
            self.waiting_queue,
            registration_number,
            vehicle_type,
            self.allocation_strategy,
        )
        if not result.success:
            return result

        try:
            self._sync()
        except PersistenceError as exc:
            # Roll the in-memory entry back so memory matches the database.
            if result.session:
                self.session_index.remove_session(result.session.session_id)
                db_id = getattr(result.session, 'db_id', None)
                if db_id is not None:
                    # Compensate for a session row written before the failure.
                    try:
                        get_supabase_service().delete_session(db_id)
                    except _DB_ERRORS as del_exc:
                        logger.error(
                            "Could not remove orphan session %s: %s",
                            db_id, del_exc,
                        )
            if result.slot:
                self.slot_registry.release_slot(result.slot.slot_number)
            logger.error(
                "Entry persistence failed for %s: %s", registration_number, exc
            )
            return EntryResult(
                success=False,
                message=f"Database error recording entry: {exc}",
            )
        return result

    def exit_vehicle(self, registration_number: str, exit_time: Optional[datetime] = None) -> ExitResult:
        """
        Process vehicle exit: record exit time, duration and fee.

        The recorded exit is persisted first. For free parking (fee 0) the
        slot is then released, the session completed and the waiting queue
        served. For fee > 0 the slot stays occupied until payment.

        Args:
            registration_number: Vehicle registration
            exit_time: Exit timestamp

        Returns:
            ExitResult with fee and details
        """
        result = process_vehicle_exit(
            self.slot_registry,
            self.vehicle_registry,
            self.session_index,
            self.waiting_queue,
            registration_number,
            exit_time,
        )
        if not result.success:
            return result

        session = result.session

        # Persist the recorded exit (duration/fee) before completing.
        try:
            self._sync()
        except PersistenceError as exc:
            session.exit_time = None
            session.duration_minutes = 0
            session.amount_due = 0
            logger.error(
                "Exit persistence failed for %s: %s", registration_number, exc
            )
            return ExitResult(
                success=False,
                message=f"Database error recording exit: {exc}",
            )

        # Free parking: no payment step. Release the slot and complete the
        # session, persist that, and only then serve the waiting queue.
        if not result.payment_required:
            if result.slot:
                self.slot_registry.release_slot(session.slot_id)
            session.complete(
                session.exit_time, session.duration_minutes, session.amount_due
            )
            try:
                self._sync()
            except PersistenceError as exc:
                session.status = SessionStatus.ACTIVE
                if result.slot:
                    result.slot.occupy()
                logger.error(
                    "Free-exit completion failed for %s: %s",
                    registration_number, exc,
                )
                return ExitResult(
                    success=False,
                    message=f"Database error completing free exit: {exc}",
                )
            queued_message = self._serve_waiting_queue()
            result.message = f"Free parking (KSh 0). Exit authorized.{queued_message}"
        return result

    def calculate_fee(self, session_id: int, duration_override: Optional[int] = None) -> Dict[str, Any]:
        """
        Calculate fee for a session.

        Args:
            session_id: Session identifier
            duration_override: Optional duration override in minutes

        Returns:
            Fee calculation details
        """
        session = self.session_index.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if duration_override is not None:
            duration = duration_override
        else:
            duration = calculate_duration_minutes(session.entry_time, session.exit_time)

        vehicle = self.vehicle_registry.get_vehicle(session.vehicle_id)
        vehicle_type = vehicle.vehicle_type if vehicle else "CAR"

        fee = calculate_parking_fee(vehicle_type, duration)
        fee_structure = DEFAULT_FEE_STRUCTURES.get(vehicle_type, DEFAULT_FEE_STRUCTURES['CAR'])

        return {
            'session_id': session_id,
            'vehicle_type': vehicle_type,
            'duration_minutes': duration,
            'fee_ksh': fee,
            'rate_used': {
                'free_minutes': fee_structure.free_minutes,
                'two_hour_rate': fee_structure.two_hour_rate,
                'four_hour_rate': fee_structure.four_hour_rate,
                'six_hour_rate': fee_structure.six_hour_rate,
                'over_six_hour_rate': fee_structure.over_six_hour_rate,
            }
        }

    def process_payment(self, session_id: int, amount: int, payment_method: str = "CASH", transaction_reference: Optional[str] = None) -> PaymentResult:
        """
        Process payment for a session, then release the slot, complete
        the session and serve the waiting queue (required exit workflow).

        Only CASH payments are enabled for now; other methods will be
        configured later. This is a manual cash record, not a payment
        gateway integration.

        Order of operations (database is the source of truth):
        1. Validate the payment (pure algorithm)
        2. Ensure the session row is synced (FK target exists)
        3. Insert the cash payment row
        4. Release slot + complete session in memory, then persist
        5. Serve the FIFO queue with the freed slot

        Args:
            session_id: Session identifier
            amount: Payment amount in KSh (validated against amount due)
            payment_method: Payment method; only "CASH" is enabled for now
            transaction_reference: Optional transaction reference

        Returns:
            PaymentResult with payment details and barrier state
        """
        session = self.session_index.get_session(session_id)
        if not session:
            return PaymentResult(
                success=False,
                message=f"Session {session_id} not found",
            )

        result = process_payment(session, amount, payment_method, transaction_reference)
        if not result.success:
            return result

        payment = result.payment

        # Make sure the session row exists in the database first (FK).
        try:
            self._sync()
        except PersistenceError as exc:
            logger.error("Payment pre-sync failed for session %s: %s", session_id, exc)
            return PaymentResult(
                success=False,
                message=f"Database error: {exc}",
            )

        # 1. Insert the cash payment row.
        if self.persistence_enabled:
            sb = get_supabase_service()
            try:
                row = sb.insert_payment(
                    session.db_id,
                    payment.amount,
                    payment.payment_method,
                    payment.transaction_reference,
                    payment.payment_time,
                )
                payment.db_id = row['id']
            except _DB_ERRORS as exc:
                logger.error(
                    "Payment insert failed for session %s: %s", session_id, exc
                )
                return PaymentResult(
                    success=False,
                    message=f"Database error recording payment: {exc}",
                )

        # 2. Release the slot and complete the session in memory.
        if session.slot_id:
            self.slot_registry.release_slot(session.slot_id)
        session.complete(
            session.exit_time, session.duration_minutes, session.amount_due
        )

        # 3. Persist the release + completion.
        try:
            self._sync()
        except PersistenceError as exc:
            # Roll memory back to the pre-payment state.
            session.status = SessionStatus.ACTIVE
            slot = self.slot_registry.get_slot(session.slot_id)
            if slot:
                slot.occupy()
            # Compensate for the already-inserted payment row (best effort).
            if getattr(payment, 'db_id', None) is not None:
                try:
                    get_supabase_service().delete_payment(payment.db_id)
                except _DB_ERRORS as del_exc:
                    logger.error(
                        "Could not remove orphan payment %s: %s",
                        payment.db_id, del_exc,
                    )
            logger.error(
                "Payment completion sync failed for session %s: %s",
                session_id, exc,
            )
            return PaymentResult(
                success=False,
                message=f"Database error completing session: {exc}",
            )

        # 4. Serve the FIFO queue with the freed slot.
        queued_message = self._serve_waiting_queue()
        result.message = f"Payment recorded. Exit authorized.{queued_message}"
        return result

    def authorize_exit(self, payment_successful: bool, session_completed: bool) -> BarrierState:
        """Authorize exit barrier."""
        return authorize_exit_barrier(payment_successful, session_completed)

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def get_active_sessions(self) -> List[Dict[str, Any]]:
        """Get all active sessions with details."""
        sessions = self.session_index.get_active_sessions()
        result = []
        for session in sessions:
            vehicle = self.vehicle_registry.get_vehicle(session.vehicle_id)
            slot = self.slot_registry.get_slot(session.slot_id)
            duration = calculate_duration_minutes(session.entry_time)
            result.append({
                'session_id': session.session_id,
                'vehicle': vehicle.to_dict() if vehicle else {'registration_number': session.vehicle_id},
                'slot': slot.to_dict() if slot else {'slot_number': session.slot_id},
                'entry_time': session.entry_time.isoformat(),
                'duration_minutes': duration,
            })
        return result

    def get_session_detail(self, session_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed session information."""
        session = self.session_index.get_session(session_id)
        if not session:
            return None

        vehicle = self.vehicle_registry.get_vehicle(session.vehicle_id)
        slot = self.slot_registry.get_slot(session.slot_id)

        return {
            'session': session.to_dict(),
            'vehicle': vehicle.to_dict() if vehicle else None,
            'slot': slot.to_dict() if slot else None,
        }

    def get_waiting_queue(self) -> List[Dict[str, Any]]:
        """Get current waiting queue."""
        return self.waiting_queue.get_all()

    def get_slot_grid(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get slots grouped by location for display."""
        slots = self.slot_registry.get_all_slots()
        locations = {}
        for slot in slots:
            loc = slot.location or 'General'
            if loc not in locations:
                locations[loc] = []
            locations[loc].append({
                'slot_number': slot.slot_number,
                'status': slot.status.value,
            })
        return locations

    def get_stats(self) -> Dict[str, Any]:
        """Get overall parking statistics."""
        slot_stats = self.slot_registry.get_stats()
        active_sessions = self.session_index.get_active_sessions()

        return {
            'slots': slot_stats,
            'active_sessions': len(active_sessions),
            'waiting_queue': self.waiting_queue.size(),
            'registered_vehicles': len(self.vehicle_registry),
        }


# Global service instance (singleton pattern for Flask)
_parking_service: Optional[ParkingService] = None


def get_parking_service() -> ParkingService:
    """Get or create the global parking service instance."""
    global _parking_service
    if _parking_service is None:
        _parking_service = ParkingService()
    return _parking_service


def init_parking_service(slots_data: List[Dict], vehicles_data: List[Dict], sessions_data: List[Dict]) -> ParkingService:
    """Initialize the global parking service with database data."""
    global _parking_service
    _parking_service = ParkingService()
    _parking_service.initialize_from_database(slots_data, vehicles_data, sessions_data)
    return _parking_service


class SupabaseService:
    """
    Supabase (PostgreSQL) data access for the parking service.

    Read/write methods raise postgrest/httpx exceptions on failure -
    callers decide how to roll back (see ParkingService._sync). The
    startup loaders (load_*) are wrapped by initialize_from_supabase,
    which converts failures into the documented in-memory fallback.
    """

    def __init__(self):
        self.client = None
        self._connect()

    def _connect(self) -> None:
        """Initialize Supabase client from environment (never logged)."""
        load_dotenv()
        try:
            from supabase import create_client, Client
        except ImportError:
            logger.warning("supabase package not installed; persistence disabled.")
            return
        url = os.environ.get('SUPABASE_URL')
        key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('SUPABASE_KEY')
        if not url or not key:
            logger.warning(
                "SUPABASE_URL/SUPABASE_KEY not set in .env; persistence disabled."
            )
            return
        try:
            self.client: Client = create_client(url, key)
        except Exception as exc:
            logger.warning("Could not create Supabase client: %s", exc)

    def is_connected(self) -> bool:
        """Check if Supabase client is available."""
        return self.client is not None

    # ------------------------------------------------------------------
    # Startup loaders
    # ------------------------------------------------------------------
    def load_slots(self) -> List[Dict]:
        """Load all parking slots."""
        response = (
            self.client.table('parking_slots')
            .select('*')
            .order('slot_number')
            .execute()
        )
        return response.data or []

    def load_vehicles(self) -> List[Dict]:
        """Load all registered vehicles."""
        response = (
            self.client.table('vehicles')
            .select('*')
            .order('registration_number')
            .execute()
        )
        return response.data or []

    def load_active_sessions(self) -> List[Dict]:
        """Load all active parking sessions."""
        response = (
            self.client.table('parking_sessions')
            .select('*')
            .eq('status', 'ACTIVE')
            .order('entry_time')
            .execute()
        )
        return response.data or []

    def load_all_session_ids(self) -> List[int]:
        """Load every session id (active and completed)."""
        response = (
            self.client.table('parking_sessions')
            .select('id')
            .execute()
        )
        return [row['id'] for row in (response.data or [])]

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def get_slot_by_number(self, slot_number: str) -> Optional[Dict]:
        """Get one slot row by its slot number."""
        response = (
            self.client.table('parking_slots')
            .select('*')
            .eq('slot_number', slot_number)
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None

    def create_slot(self, slot_number: str, location: str = "") -> Dict:
        """Insert a slot row (used when memory has a slot the DB lacks)."""
        response = (
            self.client.table('parking_slots')
            .insert({
                'slot_number': slot_number,
                'location': location,
                'status': 'AVAILABLE',
            })
            .execute()
        )
        return response.data[0]

    def update_slot_status(self, slot_number: str, status: str) -> bool:
        """Update a slot's status. Returns False when the row is missing."""
        response = (
            self.client.table('parking_slots')
            .update({'status': status})
            .eq('slot_number', slot_number)
            .execute()
        )
        return len(response.data or []) > 0

    # ------------------------------------------------------------------
    # Vehicles
    # ------------------------------------------------------------------
    def get_or_create_vehicle(self, registration_number: str, vehicle_type: str) -> Dict:
        """Return the vehicle row, creating it when it does not exist yet."""
        # Look up both formats: canonical 'KAA 123A' (fixed SQL trigger) and
        # the stripped legacy format 'KAA123A' written by the previous
        # trigger, until docs/database.sql has been re-applied.
        response = (
            self.client.table('vehicles')
            .select('*')
            .in_('registration_number', [
                registration_number, registration_number.replace(' ', ''),
            ])
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]
        response = (
            self.client.table('vehicles')
            .insert({
                'registration_number': registration_number,
                'vehicle_type': vehicle_type,
            })
            .execute()
        )
        return response.data[0]

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    def insert_session(self, vehicle_db_id: int, slot_db_id: int, entry_time: datetime) -> Dict:
        """Insert a new ACTIVE session and return the created row."""
        response = (
            self.client.table('parking_sessions')
            .insert({
                'vehicle_id': vehicle_db_id,
                'slot_id': slot_db_id,
                'entry_time': _iso_utc(entry_time),
                'status': 'ACTIVE',
            })
            .execute()
        )
        return response.data[0]

    def update_session(
        self,
        session_db_id: int,
        *,
        exit_time: Optional[datetime],
        duration_minutes: int,
        amount_due: int,
        status: str,
    ) -> None:
        """Update exit fields and status of a session (None clears exit)."""
        response = (
            self.client.table('parking_sessions')
            .update({
                'exit_time': _iso_utc(exit_time) if exit_time else None,
                'duration_minutes': duration_minutes,
                'amount_due': amount_due,
                'status': status,
            })
            .eq('id', session_db_id)
            .execute()
        )
        if not response.data:
            raise PersistenceError(f"Session {session_db_id} not found for update")

    def delete_session(self, session_db_id: int) -> None:
        """Delete a session row (compensating action after a failure)."""
        (
            self.client.table('parking_sessions')
            .delete()
            .eq('id', session_db_id)
            .execute()
        )

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------
    def insert_payment(
        self,
        session_db_id: int,
        amount: int,
        payment_method: str,
        transaction_reference: str,
        payment_time: datetime,
    ) -> Dict:
        """Insert a cash payment row and return it."""
        response = (
            self.client.table('payments')
            .insert({
                'session_id': session_db_id,
                'amount': amount,
                'payment_method': payment_method,
                'payment_status': 'PAID',
                'transaction_reference': transaction_reference,
                'payment_time': _iso_utc(payment_time),
            })
            .execute()
        )
        return response.data[0]

    def delete_payment(self, payment_db_id: int) -> None:
        """Delete a payment row (compensating action after a failure)."""
        (
            self.client.table('payments')
            .delete()
            .eq('id', payment_db_id)
            .execute()
        )

    def get_payments_for_session(self, session_db_id: int) -> List[Dict]:
        """Get all payments for a session, newest first."""
        response = (
            self.client.table('payments')
            .select('*')
            .eq('session_id', session_db_id)
            .order('payment_time', desc=True)
            .execute()
        )
        return response.data or []


# Global Supabase service instance
_supabase_service: Optional[SupabaseService] = None


def get_supabase_service() -> SupabaseService:
    """Get or create the global Supabase service instance."""
    global _supabase_service
    if _supabase_service is None:
        _supabase_service = SupabaseService()
    return _supabase_service
