"""
ParkFlow Kenya - Data Structures Module.

This module implements the core data structures used by the parking algorithms.
All structures are designed for efficient parking management operations.

Data Structures:
1. ParkingSlotRegistry - Dictionary-based slot management (O(1) lookup)
2. VehicleRegistry - Hash-based vehicle lookup (O(1) average)
3. WaitingQueue - FIFO queue for waiting vehicles
4. SessionIndex - Active session tracking

Time Complexities:
- Slot allocation: O(n) worst case, O(1) best case (first available)
- Vehicle lookup: O(1) average
- Session lookup: O(1) average
- Fee calculation: O(1)
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
import threading


class SlotStatus(Enum):
    """Parking slot status enumeration."""
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    MAINTENANCE = "MAINTENANCE"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"


class SessionStatus(Enum):
    """Parking session status enumeration."""
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PaymentStatus(Enum):
    """Payment status enumeration."""
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"


@dataclass
class ParkingSlot:
    """
    Represents a parking slot in the facility.

    Attributes:
        slot_number: Unique identifier (e.g., 'A01', 'B12')
        status: Current slot status
        location: Physical location description
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """
    slot_number: str
    status: SlotStatus = SlotStatus.AVAILABLE
    location: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def is_available(self) -> bool:
        """Check if slot is available for parking."""
        return self.status == SlotStatus.AVAILABLE

    def occupy(self) -> None:
        """Mark slot as occupied."""
        self.status = SlotStatus.OCCUPIED
        self.updated_at = datetime.utcnow()

    def release(self) -> None:
        """Mark slot as available."""
        self.status = SlotStatus.AVAILABLE
        self.updated_at = datetime.utcnow()

    def set_maintenance(self) -> None:
        """Mark slot as under maintenance."""
        self.status = SlotStatus.MAINTENANCE
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'slot_number': self.slot_number,
            'status': self.status.value,
            'location': self.location,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ParkingSlot':
        """Create ParkingSlot from dictionary."""
        slot = cls(
            slot_number=data['slot_number'],
            status=SlotStatus(data.get('status', 'AVAILABLE')),
            location=data.get('location', ''),
        )
        if 'created_at' in data:
            slot.created_at = datetime.fromisoformat(data['created_at'])
        if 'updated_at' in data:
            slot.updated_at = datetime.fromisoformat(data['updated_at'])
        return slot


@dataclass
class Vehicle:
    """
    Represents a registered vehicle.

    Attributes:
        registration_number: Unique vehicle registration (Kenyan format)
        vehicle_type: Type of vehicle for fee calculation
        created_at: Registration timestamp
        updated_at: Last update timestamp
    """
    registration_number: str
    vehicle_type: str = "CAR"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'registration_number': self.registration_number,
            'vehicle_type': self.vehicle_type,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Vehicle':
        """Create Vehicle from dictionary."""
        vehicle = cls(
            registration_number=data['registration_number'],
            vehicle_type=data.get('vehicle_type', 'CAR'),
        )
        if 'created_at' in data:
            vehicle.created_at = datetime.fromisoformat(data['created_at'])
        if 'updated_at' in data:
            vehicle.updated_at = datetime.fromisoformat(data['updated_at'])
        return vehicle


@dataclass
class ParkingSession:
    """
    Represents a parking session.

    Attributes:
        session_id: Unique session identifier
        vehicle_id: Vehicle registration number
        slot_id: Parking slot number
        entry_time: When vehicle entered
        exit_time: When vehicle exited (None if active)
        duration_minutes: Total parking duration
        amount_due: Calculated fee in KSh
        status: Session status
        created_at: Creation timestamp
        updated_at: Last update timestamp
    """
    session_id: int
    vehicle_id: str
    slot_id: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    duration_minutes: int = 0
    amount_due: int = 0
    status: SessionStatus = SessionStatus.ACTIVE
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def calculate_duration(self, current_time: Optional[datetime] = None) -> int:
        """
        Calculate parking duration in minutes.

        Uses ceiling rounding so that 30:01 -> 31 minutes (fee boundary).

        Args:
            current_time: Time to calculate duration up to (default: now).

        Returns:
            Duration in minutes (minimum 1, rounded up to nearest minute).
        """
        from datetime import timezone
        if current_time is None:
            current_time = datetime.now(timezone.utc)
        end_time = self.exit_time if self.exit_time else current_time
        # Normalize to UTC and strip tz for subtraction
        e_start = self.entry_time
        if e_start.tzinfo is not None:
            e_start = e_start.astimezone(timezone.utc).replace(tzinfo=None)
        e_end = end_time
        if e_end.tzinfo is not None:
            e_end = e_end.astimezone(timezone.utc).replace(tzinfo=None)
        delta = e_end - e_start
        total = delta.total_seconds()
        minutes = int(total // 60)
        if total % 60 > 0:
            minutes += 1
        return max(1, minutes)

    def is_active(self) -> bool:
        """Check if session is currently active."""
        return self.status == SessionStatus.ACTIVE

    def complete(self, exit_time: datetime, duration_minutes: int, amount_due: int) -> None:
        """Mark session as completed."""
        self.exit_time = exit_time
        self.duration_minutes = duration_minutes
        self.amount_due = amount_due
        self.status = SessionStatus.COMPLETED
        self.updated_at = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'session_id': self.session_id,
            'vehicle_id': self.vehicle_id,
            'slot_id': self.slot_id,
            'entry_time': self.entry_time.isoformat(),
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'duration_minutes': self.duration_minutes,
            'amount_due': self.amount_due,
            'status': self.status.value,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ParkingSession':
        """Create ParkingSession from dictionary."""
        session = cls(
            session_id=data['session_id'],
            vehicle_id=data['vehicle_id'],
            slot_id=data['slot_id'],
            entry_time=datetime.fromisoformat(data['entry_time']),
            duration_minutes=data.get('duration_minutes', 0),
            amount_due=data.get('amount_due', 0),
            status=SessionStatus(data.get('status', 'ACTIVE')),
        )
        if data.get('exit_time'):
            session.exit_time = datetime.fromisoformat(data['exit_time'])
        if 'created_at' in data:
            session.created_at = datetime.fromisoformat(data['created_at'])
        if 'updated_at' in data:
            session.updated_at = datetime.fromisoformat(data['updated_at'])
        return session


@dataclass
class Payment:
    """
    Represents a payment transaction.

    Attributes:
        payment_id: Unique payment identifier
        session_id: Associated parking session ID
        amount: Amount paid in KSh
        payment_method: Payment method used
        status: Payment status
        transaction_reference: Unique transaction reference
        payment_time: When payment was processed
        created_at: Creation timestamp
    """
    payment_id: int
    session_id: int
    amount: int
    payment_method: str
    status: PaymentStatus = PaymentStatus.PENDING
    transaction_reference: str = ""
    payment_time: datetime = field(default_factory=datetime.utcnow)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def mark_paid(self) -> None:
        """Mark payment as successful."""
        self.status = PaymentStatus.PAID
        self.payment_time = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'payment_id': self.payment_id,
            'session_id': self.session_id,
            'amount': self.amount,
            'payment_method': self.payment_method,
            'status': self.status.value,
            'transaction_reference': self.transaction_reference,
            'payment_time': self.payment_time.isoformat(),
            'created_at': self.created_at.isoformat(),
        }


class ParkingSlotRegistry:
    """
    Thread-safe registry for parking slots using dictionary for O(1) lookup.

    This is the primary data structure for slot management.
    Uses a dictionary keyed by slot_number for efficient access.

    Time Complexity:
        - get_slot: O(1) average
        - add_slot: O(1) average
        - remove_slot: O(1) average
        - get_available_slots: O(n) where n = total slots
        - allocate_slot: O(n) worst case (sequential search)
    """

    def __init__(self):
        self._slots: Dict[str, ParkingSlot] = {}
        self._lock = threading.RLock()

    def add_slot(self, slot: ParkingSlot) -> bool:
        """
        Add a parking slot to the registry.

        Args:
            slot: ParkingSlot to add

        Returns:
            True if added, False if slot_number already exists
        """
        with self._lock:
            if slot.slot_number in self._slots:
                return False
            self._slots[slot.slot_number] = slot
            return True

    def get_slot(self, slot_number: str) -> Optional[ParkingSlot]:
        """
        Get a slot by its number.

        Args:
            slot_number: Slot identifier

        Returns:
            ParkingSlot if found, None otherwise
        """
        with self._lock:
            return self._slots.get(slot_number)

    def remove_slot(self, slot_number: str) -> bool:
        """
        Remove a slot from the registry.

        Args:
            slot_number: Slot identifier

        Returns:
            True if removed, False if not found
        """
        with self._lock:
            if slot_number in self._slots:
                del self._slots[slot_number]
                return True
            return False

    def get_all_slots(self) -> List[ParkingSlot]:
        """Get all slots (thread-safe copy)."""
        with self._lock:
            return list(self._slots.values())

    def get_available_slots(self) -> List[ParkingSlot]:
        """
        Get all available slots.

        Time Complexity: O(n) where n = total slots

        Returns:
            List of available ParkingSlot objects
        """
        with self._lock:
            return [slot for slot in self._slots.values() if slot.is_available()]

    def get_occupied_slots(self) -> List[ParkingSlot]:
        """Get all occupied slots."""
        with self._lock:
            return [slot for slot in self._slots.values() if slot.status == SlotStatus.OCCUPIED]

    def get_slots_by_status(self, status: SlotStatus) -> List[ParkingSlot]:
        """Get all slots with a specific status."""
        with self._lock:
            return [slot for slot in self._slots.values() if slot.status == status]

    def allocate_first_available(self) -> Optional[ParkingSlot]:
        """
        Find and allocate the first available slot (sequential allocation).

        Time Complexity: O(n) worst case where n = total slots
        Space Complexity: O(1)

        Returns:
            Allocated ParkingSlot or None if no slots available
        """
        with self._lock:
            # Sort by slot_number for consistent allocation order
            for slot in sorted(self._slots.values(), key=lambda s: s.slot_number):
                if slot.is_available():
                    slot.occupy()
                    return slot
            return None

    def release_slot(self, slot_number: str) -> bool:
        """
        Release a slot (mark as available).

        Args:
            slot_number: Slot to release

        Returns:
            True if released, False if not found or not occupied
        """
        with self._lock:
            slot = self._slots.get(slot_number)
            if slot and slot.status == SlotStatus.OCCUPIED:
                slot.release()
                return True
            return False

    def get_stats(self) -> Dict[str, int]:
        """Get slot statistics."""
        with self._lock:
            stats = {status.value: 0 for status in SlotStatus}
            for slot in self._slots.values():
                stats[slot.status.value] += 1
            stats['total'] = len(self._slots)
            return stats

    def __len__(self) -> int:
        with self._lock:
            return len(self._slots)

    def __contains__(self, slot_number: str) -> bool:
        with self._lock:
            return slot_number in self._slots


class VehicleRegistry:
    """
    Thread-safe registry for vehicles using dictionary for O(1) lookup.

    Primary data structure for vehicle registration and lookup.
    Uses dictionary keyed by normalized registration_number.

    Time Complexity:
        - register_vehicle: O(1) average
        - get_vehicle: O(1) average
        - vehicle_exists: O(1) average
    """

    def __init__(self):
        self._vehicles: Dict[str, Vehicle] = {}
        self._lock = threading.RLock()

    @staticmethod
    def normalize_registration(reg: str) -> str:
        """Normalize registration number to standard format."""
        clean = reg.replace(' ', '').upper()
        if len(clean) >= 4:
            return clean[:3] + ' ' + clean[3:]
        return clean

    def register_vehicle(self, vehicle: Vehicle) -> bool:
        """
        Register a new vehicle.

        Args:
            vehicle: Vehicle to register

        Returns:
            True if registered, False if already exists
        """
        with self._lock:
            norm_reg = self.normalize_registration(vehicle.registration_number)
            if norm_reg in self._vehicles:
                return False
            vehicle.registration_number = norm_reg
            self._vehicles[norm_reg] = vehicle
            return True

    def get_vehicle(self, registration_number: str) -> Optional[Vehicle]:
        """
        Get a vehicle by registration number.

        Args:
            registration_number: Vehicle registration (any format)

        Returns:
            Vehicle if found, None otherwise
        """
        with self._lock:
            norm_reg = self.normalize_registration(registration_number)
            return self._vehicles.get(norm_reg)

    def vehicle_exists(self, registration_number: str) -> bool:
        """Check if vehicle is registered."""
        with self._lock:
            norm_reg = self.normalize_registration(registration_number)
            return norm_reg in self._vehicles

    def update_vehicle_type(self, registration_number: str, vehicle_type: str) -> bool:
        """Update vehicle type."""
        with self._lock:
            norm_reg = self.normalize_registration(registration_number)
            vehicle = self._vehicles.get(norm_reg)
            if vehicle:
                vehicle.vehicle_type = vehicle_type
                vehicle.updated_at = datetime.utcnow()
                return True
            return False

    def get_all_vehicles(self) -> List[Vehicle]:
        """Get all registered vehicles."""
        with self._lock:
            return list(self._vehicles.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._vehicles)

    def __contains__(self, registration_number: str) -> bool:
        with self._lock:
            norm_reg = self.normalize_registration(registration_number)
            return norm_reg in self._vehicles


class WaitingQueue:
    """
    FIFO queue for vehicles waiting for parking slots.

    Uses collections.deque for O(1) append and popleft operations.

    Time Complexity:
        - enqueue: O(1)
        - dequeue: O(1)
        - peek: O(1)
        - size: O(1)
    """

    def __init__(self):
        self._queue: deque = deque()
        self._lock = threading.RLock()

    def enqueue(self, vehicle_reg: str, vehicle_type: str = "CAR") -> None:
        """
        Add a vehicle to the waiting queue.

        Args:
            vehicle_reg: Vehicle registration number
            vehicle_type: Type of vehicle
        """
        with self._lock:
            self._queue.append({
                'registration_number': vehicle_reg,
                'vehicle_type': vehicle_type,
                'queued_at': datetime.utcnow(),
            })

    def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        Remove and return the next vehicle in queue.

        Returns:
            Vehicle info dict or None if queue empty
        """
        with self._lock:
            if self._queue:
                return self._queue.popleft()
            return None

    def peek(self) -> Optional[Dict[str, Any]]:
        """View next vehicle without removing."""
        with self._lock:
            if self._queue:
                return self._queue[0]
            return None

    def remove(self, registration_number: str) -> bool:
        """
        Remove a specific vehicle from queue.

        Time Complexity: O(n) where n = queue size

        Args:
            registration_number: Vehicle to remove

        Returns:
            True if removed, False if not found
        """
        with self._lock:
            norm_reg = VehicleRegistry.normalize_registration(registration_number)
            for i, item in enumerate(self._queue):
                if VehicleRegistry.normalize_registration(item['registration_number']) == norm_reg:
                    self._queue.remove(item)
                    return True
            return False

    def size(self) -> int:
        """Get queue size."""
        with self._lock:
            return len(self._queue)

    def is_empty(self) -> bool:
        """Check if queue is empty."""
        with self._lock:
            return len(self._queue) == 0

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all queued vehicles."""
        with self._lock:
            return list(self._queue)


class SessionIndex:
    """
    Index for active parking sessions.

    Provides O(1) lookup for active sessions by vehicle and slot.
    Uses two dictionaries for bidirectional indexing.

    Time Complexity:
        - add_session: O(1) average
        - get_session: O(1) average
        - get_by_vehicle: O(1) average
        - get_by_slot: O(1) average
        - remove_session: O(1) average
    """

    def __init__(self):
        self._by_id: Dict[int, ParkingSession] = {}
        self._by_vehicle: Dict[str, int] = {}  # vehicle_id -> session_id
        self._by_slot: Dict[str, int] = {}     # slot_id -> session_id
        # Highest session id known to the database (completed sessions are
        # not loaded into memory, so new ids must start above this floor).
        self.id_floor: int = 0
        self._lock = threading.RLock()

    def add_session(self, session: ParkingSession) -> None:
        """Add a session to the index."""
        with self._lock:
            self._by_id[session.session_id] = session
            self._by_vehicle[session.vehicle_id] = session.session_id
            self._by_slot[session.slot_id] = session.session_id

    def get_session(self, session_id: int) -> Optional[ParkingSession]:
        """Get session by ID."""
        with self._lock:
            return self._by_id.get(session_id)

    def get_by_vehicle(self, vehicle_id: str) -> Optional[ParkingSession]:
        """Get active session for a vehicle."""
        with self._lock:
            session_id = self._by_vehicle.get(vehicle_id)
            if session_id:
                return self._by_id.get(session_id)
            return None

    def get_by_slot(self, slot_id: str) -> Optional[ParkingSession]:
        """Get active session for a slot."""
        with self._lock:
            session_id = self._by_slot.get(slot_id)
            if session_id:
                return self._by_id.get(session_id)
            return None

    def remove_session(self, session_id: int) -> bool:
        """Remove a session from the index."""
        with self._lock:
            session = self._by_id.get(session_id)
            if session:
                del self._by_id[session_id]
                self._by_vehicle.pop(session.vehicle_id, None)
                self._by_slot.pop(session.slot_id, None)
                return True
            return False

    def get_active_sessions(self) -> List[ParkingSession]:
        """Get all active sessions."""
        with self._lock:
            return [s for s in self._by_id.values() if s.is_active()]

    def get_all_sessions(self) -> List[ParkingSession]:
        """Get all sessions (active and completed)."""
        with self._lock:
            return list(self._by_id.values())

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_id)