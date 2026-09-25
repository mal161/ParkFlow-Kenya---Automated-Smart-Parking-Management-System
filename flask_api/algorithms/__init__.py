"""
ParkFlow Kenya - Algorithms Module.

This module implements the core algorithms for parking management.
All algorithms are documented with their purpose, complexity, and usage.

Algorithms Implemented:
1. Slot Allocation - First-fit sequential allocation
2. Availability Checking - Real-time slot status
3. Fee Calculation - Tiered pricing with boundary handling
4. Duration Calculation - Precise minute-based computation
5. Vehicle Lookup - Hash-based O(1) registration lookup
6. Session Management - Entry/exit processing
7. Waiting Queue Management - FIFO processing
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

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


# =============================================================================
# FEE CALCULATION ALGORITHM
# =============================================================================

@dataclass
class FeeStructure:
    """
    Parking fee structure for a vehicle type.

    Fee Rules (CLIENT REQUIREMENTS - DO NOT MODIFY):
    - 0-30 minutes: KSh 0 (FREE)
    - 31-120 minutes: KSh 50
    - 121-240 minutes: KSh 100
    - 241-360 minutes: KSh 300
    - 361+ minutes: KSh 500

    Boundary Cases (MUST BE TESTED):
    - 30 min  -> KSh 0
    - 31 min  -> KSh 50
    - 120 min -> KSh 50
    - 121 min -> KSh 100
    - 240 min -> KSh 100
    - 241 min -> KSh 300
    - 360 min -> KSh 300
    - 361 min -> KSh 500
    """
    free_minutes: int = 30
    two_hour_rate: int = 50
    four_hour_rate: int = 100
    six_hour_rate: int = 300
    over_six_hour_rate: int = 500

    def calculate_fee(self, duration_minutes: int) -> int:
        """
        Calculate parking fee based on duration in minutes.

        Uses minute-based calculation to avoid boundary errors.
        Fee tiers are evaluated in order.

        Time Complexity: O(1)
        Space Complexity: O(1)

        Args:
            duration_minutes: Parking duration in minutes (must be >= 1)

        Returns:
            Fee in KSh (integer)
        """
        if duration_minutes <= self.free_minutes:
            return 0
        elif duration_minutes <= 120:  # 2 hours
            return self.two_hour_rate
        elif duration_minutes <= 240:  # 4 hours
            return self.four_hour_rate
        elif duration_minutes <= 360:  # 6 hours
            return self.six_hour_rate
        else:
            return self.over_six_hour_rate

    def get_fee_tiers(self) -> List[Dict[str, Any]]:
        """Get human-readable fee tiers for display."""
        return [
            {'range': f'0-{self.free_minutes} min', 'fee': 0, 'label': 'Free'},
            {'range': f'{self.free_minutes + 1}-120 min', 'fee': self.two_hour_rate, 'label': 'Up to 2 hours'},
            {'range': '121-240 min', 'fee': self.four_hour_rate, 'label': 'Up to 4 hours'},
            {'range': '241-360 min', 'fee': self.six_hour_rate, 'label': 'Up to 6 hours'},
            {'range': '361+ min', 'fee': self.over_six_hour_rate, 'label': 'Over 6 hours'},
        ]


# Default fee structures by vehicle type
DEFAULT_FEE_STRUCTURES: Dict[str, FeeStructure] = {
    'CAR': FeeStructure(),
    'MOTORCYCLE': FeeStructure(
        free_minutes=30,
        two_hour_rate=20,
        four_hour_rate=50,
        six_hour_rate=100,
        over_six_hour_rate=200,
    ),
    'TRUCK': FeeStructure(
        free_minutes=30,
        two_hour_rate=100,
        four_hour_rate=200,
        six_hour_rate=500,
        over_six_hour_rate=1000,
    ),
    'BUS': FeeStructure(
        free_minutes=30,
        two_hour_rate=150,
        four_hour_rate=300,
        six_hour_rate=800,
        over_six_hour_rate=1500,
    ),
    'OTHER': FeeStructure(),
}


def calculate_parking_fee(vehicle_type: str, duration_minutes: int) -> int:
    """
    Calculate parking fee for a vehicle type and duration.

    This is the main fee calculation entry point.

    Time Complexity: O(1)
    Space Complexity: O(1)

    Args:
        vehicle_type: Type of vehicle (CAR, MOTORCYCLE, TRUCK, BUS, OTHER)
        duration_minutes: Parking duration in minutes

    Returns:
        Fee in KSh

    Raises:
        ValueError: If vehicle_type is unknown
    """
    fee_structure = DEFAULT_FEE_STRUCTURES.get(vehicle_type.upper())
    if not fee_structure:
        # Fallback to CAR rates
        fee_structure = DEFAULT_FEE_STRUCTURES['CAR']
    return fee_structure.calculate_fee(duration_minutes)


# =============================================================================
# DURATION CALCULATION ALGORITHM
# =============================================================================

def calculate_duration_minutes(entry_time: datetime, exit_time: Optional[datetime] = None) -> int:
    """
    Calculate parking duration in minutes.

    Uses ceiling to ensure minimum 1 minute charging.
    Handles timezone-aware datetimes correctly.
    Rounds UP to nearest minute so that 30:01 -> 31 minutes (fee applies).

    Time Complexity: O(1)
    Space Complexity: O(1)

    Args:
        entry_time: When vehicle entered (timezone-aware or naive)
        exit_time: When vehicle exited (timezone-aware or naive, default: now UTC)

    Returns:
        Duration in minutes (minimum 1, rounded up to nearest minute)
    """
    from datetime import timezone

    if exit_time is None:
        exit_time = datetime.now(timezone.utc)

    # Normalize both to UTC and strip timezone info for comparison
    if entry_time.tzinfo is not None:
        # If entry is timezone-aware, convert to UTC and strip tz
        # Use replace to strip, but only after ensuring we're working with a copy
        entry = entry_time.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        # Naive datetime - assume it's already in the correct timezone (Africa/Nairobi)
        # and strip to make it naive for subtraction
        entry = entry_time

    if exit_time.tzinfo is not None:
        # If exit is timezone-aware, convert to UTC and strip tz
        exit_ = exit_time.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        # Naive datetime - assume UTC
        exit_ = exit_time.replace(tzinfo=None) if exit_time.tzinfo is not None else exit_time

    delta = exit_ - entry
    total_seconds = delta.total_seconds()

    # Ceiling division: round up to nearest minute
    # This ensures 30:01 -> 31 minutes (fee applies at 31min)
    minutes = int(total_seconds // 60)
    if total_seconds % 60 > 0:
        minutes += 1

    return max(1, minutes)


# =============================================================================
# SLOT ALLOCATION ALGORITHM
# =============================================================================

class SlotAllocationStrategy:
    """
    Abstract base for slot allocation strategies.
    """

    def allocate(self, registry: ParkingSlotRegistry) -> Optional[ParkingSlot]:
        raise NotImplementedError


class FirstFitAllocation(SlotAllocationStrategy):
    """
    First-fit slot allocation strategy.

    Allocates the first available slot in sorted order.
    Simple, deterministic, and fair.

    Time Complexity: O(n) where n = total slots
    Space Complexity: O(1)

    Advantages:
    - Simple implementation
    - Deterministic behavior
    - Good for uniform slot layouts

    Disadvantages:
    - May not optimize for proximity to entrance
    - Sequential search in worst case
    """

    def allocate(self, registry: ParkingSlotRegistry) -> Optional[ParkingSlot]:
        """
        Allocate the first available slot.

        Args:
            registry: ParkingSlotRegistry to search

        Returns:
            Allocated ParkingSlot or None
        """
        return registry.allocate_first_available()


class NearestToEntranceAllocation(SlotAllocationStrategy):
    """
    Nearest-to-entrance allocation strategy.

    Assumes slot numbers encode proximity (e.g., A01, A02 near entrance).
    Allocates available slot with lowest sort order.

    Time Complexity: O(n log n) due to sorting, or O(n) if pre-sorted
    Space Complexity: O(1)
    """

    def allocate(self, registry: ParkingSlotRegistry) -> Optional[ParkingSlot]:
        """Allocate nearest available slot to entrance."""
        available = registry.get_available_slots()
        if not available:
            return None

        # Sort by slot number (assumes A01, A02, B01, B02 ordering)
        available.sort(key=lambda s: s.slot_number)
        slot = available[0]
        slot.occupy()
        return slot


# Default strategy
DEFAULT_ALLOCATION_STRATEGY = FirstFitAllocation()


# =============================================================================
# VEHICLE ENTRY ALGORITHM
# =============================================================================

@dataclass
class EntryResult:
    """Result of vehicle entry processing."""
    success: bool
    message: str
    session: Optional[ParkingSession] = None
    slot: Optional[ParkingSlot] = None
    vehicle: Optional[Vehicle] = None
    queued: bool = False
    queue_position: int = 0


def process_vehicle_entry(
    slot_registry: ParkingSlotRegistry,
    vehicle_registry: VehicleRegistry,
    session_index: SessionIndex,
    waiting_queue: WaitingQueue,
    registration_number: str,
    vehicle_type: str = "CAR",
    allocation_strategy: SlotAllocationStrategy = DEFAULT_ALLOCATION_STRATEGY,
) -> EntryResult:
    """
    Process vehicle entry - the core parking entry algorithm.

    Algorithm Steps:
    1. Normalize registration number
    2. Check for existing active session (prevent duplicates)
    3. Register vehicle if new
    4. Find available slot using allocation strategy
    5. If no slot available, add to waiting queue
    6. Create parking session
    7. Update slot status
    8. Index session

    Time Complexity:
        - Best case: O(1) - slot available, vehicle known
        - Worst case: O(n) - slot search + queue management
        - Vehicle lookup: O(1) average

    Space Complexity: O(1) additional

    Args:
        slot_registry: ParkingSlotRegistry instance
        vehicle_registry: VehicleRegistry instance
        session_index: SessionIndex instance
        waiting_queue: WaitingQueue instance
        registration_number: Vehicle registration
        vehicle_type: Type of vehicle
        allocation_strategy: Strategy for slot allocation

    Returns:
        EntryResult with outcome details
    """
    # Step 1: Normalize registration
    norm_reg = VehicleRegistry.normalize_registration(registration_number)

    # Step 2: Check for existing active session
    existing_session = session_index.get_by_vehicle(norm_reg)
    if existing_session and existing_session.is_active():
        return EntryResult(
            success=False,
            message=f"Vehicle {norm_reg} already has an active session",
            session=existing_session,
        )

    # Step 3: Register or get vehicle
    vehicle = vehicle_registry.get_vehicle(norm_reg)
    if not vehicle:
        vehicle = Vehicle(registration_number=norm_reg, vehicle_type=vehicle_type)
        vehicle_registry.register_vehicle(vehicle)
    elif vehicle.vehicle_type != vehicle_type:
        vehicle_registry.update_vehicle_type(norm_reg, vehicle_type)
        vehicle.vehicle_type = vehicle_type

    # Step 4: Allocate slot
    slot = allocation_strategy.allocate(slot_registry)

    if slot is None:
        # Step 5: No slots available - add to waiting queue
        waiting_queue.enqueue(norm_reg, vehicle_type)
        position = waiting_queue.size()
        return EntryResult(
            success=False,
            message="Parking full. Vehicle added to waiting queue.",
            vehicle=vehicle,
            queued=True,
            queue_position=position,
        )

    # Step 6: Create parking session
    session = ParkingSession(
        session_id=_generate_session_id(session_index),
        vehicle_id=norm_reg,
        slot_id=slot.slot_number,
        entry_time=datetime.utcnow(),
        status=SessionStatus.ACTIVE,
    )

    # Step 7 & 8: Index session
    session_index.add_session(session)

    return EntryResult(
        success=True,
        message="Vehicle registered and slot allocated successfully",
        session=session,
        slot=slot,
        vehicle=vehicle,
    )


def _generate_session_id(session_index: SessionIndex) -> int:
    """Generate a unique session ID (never below the database floor)."""
    with session_index._lock:
        return max(
            max(session_index._by_id.keys(), default=0),
            session_index.id_floor,
        ) + 1


# =============================================================================
# VEHICLE EXIT ALGORITHM
# =============================================================================

@dataclass
class ExitResult:
    """Result of vehicle exit processing."""
    success: bool
    message: str
    session: Optional[ParkingSession] = None
    slot: Optional[ParkingSlot] = None
    fee: int = 0
    duration_minutes: int = 0
    payment_required: bool = False
    exit_authorized: bool = False


def process_vehicle_exit(
    slot_registry: ParkingSlotRegistry,
    vehicle_registry: VehicleRegistry,
    session_index: SessionIndex,
    waiting_queue: WaitingQueue,
    registration_number: str,
    exit_time: Optional[Any] = None,
) -> ExitResult:
    """
    Process vehicle exit (all steps up to payment).

    Follows the required parking workflow:
    find active session -> record exit time -> calculate duration ->
    calculate fee -> display amount -> request payment.

    The slot is NOT released and the session is NOT completed at this stage.
    The service layer performs release/completion after the recorded exit
    has been persisted (or immediately when the fee is 0 - free parking).

    Algorithm Steps:
    1. Find active session for vehicle (O(1) dictionary lookup)
    2. Accept exit time as datetime or ISO-8601 string (from HTTP/JSON)
    3. Calculate duration in minutes (ceiling rounding)
    4. Calculate fee from the vehicle's fee structure (O(1))
    5. Record exit time / duration / amount due on the session
    6. Fee == 0: return exit_authorized (service releases the slot now)
    7. Fee > 0: return payment_required; slot released after payment

    Time Complexity: O(1) lookups; O(n) queue serving only when a slot is freed
    Space Complexity: O(1)

    Args:
        slot_registry: ParkingSlotRegistry instance
        vehicle_registry: VehicleRegistry instance
        session_index: SessionIndex instance
        waiting_queue: WaitingQueue instance
        registration_number: Vehicle registration
        exit_time: Exit timestamp as datetime, ISO string, or None (now)

    Returns:
        ExitResult with fee, duration and payment/authorization state
    """
    # Accept ISO-8601 strings coming from HTTP/JSON payloads
    if isinstance(exit_time, str):
        exit_time = datetime.fromisoformat(exit_time)

    if exit_time is None:
        exit_time = datetime.utcnow()

    # Step 1: Find active session
    norm_reg = VehicleRegistry.normalize_registration(registration_number)
    session = session_index.get_by_vehicle(norm_reg)

    if not session or not session.is_active():
        return ExitResult(
            success=False,
            message=f"No active session found for vehicle {norm_reg}",
        )

    # Steps 2-5: duration and fee. Reuse recorded values if exit
    # was already recorded (idempotent re-submission of the exit request).
    if session.exit_time is None:
        duration = calculate_duration_minutes(session.entry_time, exit_time)
        fee = 0
        vehicle = vehicle_registry.get_vehicle(norm_reg)
        if vehicle:
            fee = calculate_parking_fee(vehicle.vehicle_type, duration)
        # Record exit details; session stays ACTIVE until payment completes.
        session.exit_time = exit_time
        session.duration_minutes = duration
        session.amount_due = fee
        session.updated_at = datetime.utcnow()
    else:
        duration = session.duration_minutes
        fee = session.amount_due
        exit_time = session.exit_time

    slot = slot_registry.get_slot(session.slot_id)

    # Step 6: free parking - no payment needed, exit authorized.
    # The service layer releases the slot, completes the session and
    # serves the waiting queue after persisting the recorded exit.
    if fee == 0:
        return ExitResult(
            success=True,
            message=f"Duration {duration} min. Fee KSh 0 - free parking.",
            session=session,
            slot=slot,
            fee=0,
            duration_minutes=duration,
            payment_required=False,
            exit_authorized=True,
        )

    # Step 7: payment required before the barrier opens
    return ExitResult(
        success=True,
        message=(
            f"Duration {duration} min. Payment of KSh {fee} required "
            "to authorize exit."
        ),
        session=session,
        slot=slot,
        fee=fee,
        duration_minutes=duration,
        payment_required=True,
        exit_authorized=False,
    )


# =============================================================================
# PAYMENT PROCESSING ALGORITHM
# =============================================================================

@dataclass
class PaymentResult:
    """Result of payment processing."""
    success: bool
    message: str
    payment: Optional[Payment] = None
    exit_authorized: bool = False


def process_payment(
    session: ParkingSession,
    amount: int,
    payment_method: str = "CASH",
    transaction_reference: Optional[str] = None,
) -> PaymentResult:
    """
    Process payment for a parking session whose exit has been recorded.

    For now only CASH payments are enabled; other methods (M-Pesa, card)
    will be configured later. This is a manual cash record, not a real
    payment-gateway integration.

    Algorithm Steps:
    1. Validate exit time was recorded (fee already calculated)
    2. Validate session is not already completed
    3. Validate amount matches amount due (server-side, never trusted
       from client-side JavaScript)
    4. Validate payment method is currently enabled (CASH only)
    5. Create payment record marked as paid
    6. Caller releases the slot and completes the session on success

    Time Complexity: O(1)
    Space Complexity: O(1)

    Args:
        session: ParkingSession with exit time and amount due recorded
        amount: Payment amount in KSh (must equal amount due)
        payment_method: Payment method; only "CASH" is enabled for now
        transaction_reference: Optional transaction reference

    Returns:
        PaymentResult with payment details (exit_authorized=True on success)
    """
    # Validate exit has been recorded (fee calculated)
    if session.exit_time is None:
        return PaymentResult(
            success=False,
            message="Exit has not been recorded for this session yet",
        )

    # Validate session is not already completed
    if session.status != SessionStatus.ACTIVE:
        return PaymentResult(
            success=False,
            message="Session is already completed",
        )

    # Validate amount against server-calculated amount due
    if amount != session.amount_due:
        return PaymentResult(
            success=False,
            message=f"Invalid amount. Expected KSh {session.amount_due}, got KSh {amount}",
        )

    # Only CASH is enabled for now; other methods will be configured later
    if payment_method.upper() != "CASH":
        return PaymentResult(
            success=False,
            message="Only CASH payment is currently enabled",
        )

    # Generate transaction reference if not provided
    if not transaction_reference:
        import uuid
        transaction_reference = f"CASH-{uuid.uuid4().hex[:12].upper()}"

    # Create payment
    payment = Payment(
        payment_id=_generate_payment_id(),
        session_id=session.session_id,
        amount=amount,
        payment_method="CASH",
        status=PaymentStatus.PAID,
        transaction_reference=transaction_reference,
        payment_time=datetime.utcnow(),
    )
    payment.mark_paid()

    return PaymentResult(
        success=True,
        message="Payment recorded. Exit authorized.",
        payment=payment,
        exit_authorized=True,
    )


def _generate_payment_id() -> int:
    """Generate unique payment ID (simplified)."""
    import random
    return random.randint(100000, 999999)


# =============================================================================
# AVAILABILITY CHECKING ALGORITHM
# =============================================================================

@dataclass
class AvailabilityInfo:
    """Parking availability information."""
    total_slots: int
    available_slots: int
    occupied_slots: int
    maintenance_slots: int
    reserved_slots: int
    occupancy_rate: float
    available_slot_numbers: List[str]


def check_availability(slot_registry: ParkingSlotRegistry) -> AvailabilityInfo:
    """
    Check current parking availability.

    Time Complexity: O(n) where n = total slots
    Space Complexity: O(k) where k = available slots

    Args:
        slot_registry: ParkingSlotRegistry instance

    Returns:
        AvailabilityInfo with current status
    """
    slots = slot_registry.get_all_slots()
    total = len(slots)
    available = sum(1 for s in slots if s.status == SlotStatus.AVAILABLE)
    occupied = sum(1 for s in slots if s.status == SlotStatus.OCCUPIED)
    maintenance = sum(1 for s in slots if s.status == SlotStatus.MAINTENANCE)
    reserved = sum(1 for s in slots if s.status == SlotStatus.RESERVED)

    available_numbers = [s.slot_number for s in slots if s.status == SlotStatus.AVAILABLE]
    available_numbers.sort()

    occupancy_rate = (occupied / total * 100) if total > 0 else 0

    return AvailabilityInfo(
        total_slots=total,
        available_slots=available,
        occupied_slots=occupied,
        maintenance_slots=maintenance,
        reserved_slots=reserved,
        occupancy_rate=round(occupancy_rate, 2),
        available_slot_numbers=available_numbers,
    )


# =============================================================================
# BARRIER AUTHORIZATION ALGORITHM (SIMULATION)
# =============================================================================

@dataclass
class BarrierState:
    """Simulated barrier state."""
    is_open: bool
    authorized: bool
    message: str
    timestamp: datetime


def authorize_exit_barrier(payment_successful: bool, session_completed: bool) -> BarrierState:
    """
    Simulate exit barrier authorization.

    In a real system, this would interface with hardware.
    Here we simulate the logic.

    Time Complexity: O(1)
    Space Complexity: O(1)

    Args:
        payment_successful: Whether payment was successful
        session_completed: Whether session is completed

    Returns:
        BarrierState with authorization result
    """
    authorized = payment_successful and session_completed

    return BarrierState(
        is_open=authorized,
        authorized=authorized,
        message="EXIT AUTHORIZED" if authorized else "EXIT DENIED - Payment Required",
        timestamp=datetime.utcnow(),
    )