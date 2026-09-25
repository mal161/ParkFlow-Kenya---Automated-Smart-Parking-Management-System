"""
ParkFlow Kenya - Automated Tests.

Covers the requirements of Phase 16 (Automated testing):

* Fee calculation boundary cases (client requirement, must not change):
    30 min -> KSh 0,  31 min -> KSh 50,  120 min -> KSh 50,
    121 min -> KSh 100, 240 min -> KSh 100, 241 min -> KSh 300,
    360 min -> KSh 300, 361 min -> KSh 500
* Slot allocation: available slot, no available slot, multiple slots,
  releasing a slot
* Vehicle entry: valid vehicle, invalid vehicle, duplicate active
  vehicle, full parking (waiting queue)
* Vehicle exit: valid active session, vehicle not found, correct
  duration, correct fee
* Payment: successful cash payment, failed payment (wrong amount),
  non-enabled payment method, slot release after payment
* Data structures: dictionary lookup (O(1)), FIFO waiting queue
* Django ORM models: defaults, validation, duration rounding

Run from the project root:
    .venv\\Scripts\\python.exe parkflow\\manage.py test tests -v 2
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import django

# Configure Django when tests are executed directly (not via manage.py)
sys.path.insert(0, 'parkflow')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from parking.models import ParkingRate, ParkingSession, ParkingSlot
from payments.models import Payment
from vehicles.models import Vehicle

from flask_api import data_structures as ds
from flask_api.algorithms import (
    calculate_duration_minutes,
    calculate_parking_fee,
)
from flask_api.services import get_parking_service, init_parking_service


def fresh_service(slot_count: int = 5):
    """
    Recreate the global parking service with N available test slots.

    The Flask service keeps state in memory between calls, so each test
    starts from a clean, deterministic state.
    """
    service = init_parking_service([], [], [])
    for i in range(1, slot_count + 1):
        service.slot_registry.add_slot(
            ds.ParkingSlot(slot_number=f'T{i:02d}', location='Test')
        )
    return service


# =============================================================================
# FEE CALCULATION (client-specified boundaries)
# =============================================================================

class FeeCalculationTests(TestCase):
    """Fee boundary cases required by the client specification."""

    def test_fee_boundary_table(self):
        """Every required boundary case must return the specified fee."""
        boundary_cases = [
            (30, 0),
            (31, 50),
            (120, 50),
            (121, 100),
            (240, 100),
            (241, 300),
            (360, 300),
            (361, 500),
        ]
        for duration, expected_fee in boundary_cases:
            with self.subTest(duration=duration, expected_fee=expected_fee):
                fee = calculate_parking_fee('CAR', duration)
                self.assertEqual(
                    fee, expected_fee,
                    f'{duration} minutes must cost KSh {expected_fee}, '
                    f'got KSh {fee}',
                )

    def test_fee_for_each_boundary_individually(self):
        """Individual assertions (explicit per-requirement coverage)."""
        self.assertEqual(calculate_parking_fee('CAR', 30), 0)
        self.assertEqual(calculate_parking_fee('CAR', 31), 50)
        self.assertEqual(calculate_parking_fee('CAR', 120), 50)
        self.assertEqual(calculate_parking_fee('CAR', 121), 100)
        self.assertEqual(calculate_parking_fee('CAR', 240), 100)
        self.assertEqual(calculate_parking_fee('CAR', 241), 300)
        self.assertEqual(calculate_parking_fee('CAR', 360), 300)
        self.assertEqual(calculate_parking_fee('CAR', 361), 500)

    def test_django_parking_rate_matches_spec(self):
        """The Django rate model must implement the same boundaries."""
        rate = ParkingRate(vehicle_type='CAR')
        self.assertEqual(rate.calculate_fee(30), 0)
        self.assertEqual(rate.calculate_fee(31), 50)
        self.assertEqual(rate.calculate_fee(120), 50)
        self.assertEqual(rate.calculate_fee(121), 100)
        self.assertEqual(rate.calculate_fee(240), 100)
        self.assertEqual(rate.calculate_fee(241), 300)
        self.assertEqual(rate.calculate_fee(360), 300)
        self.assertEqual(rate.calculate_fee(361), 500)


class DurationCalculationTests(TestCase):
    """Duration rounding: partial minutes count as a full minute."""

    def test_exact_minutes_round_down_to_whole_minutes(self):
        entry = datetime(2026, 9, 25, 8, 0, 0)
        self.assertEqual(
            calculate_duration_minutes(entry, entry + timedelta(minutes=30)), 30
        )
        self.assertEqual(
            calculate_duration_minutes(entry, entry + timedelta(minutes=120)), 120
        )

    def test_partial_minute_rounds_up(self):
        """30 minutes 1 second must become 31 minutes (fee applies)."""
        entry = datetime(2026, 9, 25, 8, 0, 0)
        self.assertEqual(
            calculate_duration_minutes(entry, entry + timedelta(seconds=1801)), 31
        )
        self.assertEqual(
            calculate_duration_minutes(entry, entry + timedelta(minutes=120, seconds=1)),
            121,
        )

    def test_django_session_duration_rounds_up(self):
        """The Django model must round the same way as the Flask algorithm."""
        session = ParkingSession(
            entry_time=dj_timezone.now(),
            exit_time=dj_timezone.now() + timedelta(minutes=90, seconds=30),
        )
        self.assertEqual(session.calculate_duration(), 91)


# =============================================================================
# SLOT ALLOCATION
# =============================================================================

class SlotAllocationTests(TestCase):
    """First-fit slot allocation, exhaustion and release."""

    def test_allocates_first_available_slot(self):
        service = fresh_service(slot_count=3)
        result = service.enter_vehicle('KAA 111A', 'CAR')

        self.assertTrue(result.success, result.message)
        self.assertIsNotNone(result.slot)
        self.assertEqual(result.slot.slot_number, 'T01')
        self.assertEqual(result.slot.status, ds.SlotStatus.OCCUPIED)

    def test_no_available_slot_returns_none(self):
        """When every slot is occupied, allocation must fail (queue)."""
        service = fresh_service(slot_count=1)

        first = service.enter_vehicle('KAA 111A', 'CAR')
        self.assertTrue(first.success, first.message)

        second = service.enter_vehicle('KBB 222B', 'CAR')
        self.assertFalse(second.success)
        self.assertTrue(second.queued)
        self.assertEqual(second.queue_position, 1)
        self.assertEqual(service.waiting_queue.size(), 1)

    def test_multiple_slots_are_distinct(self):
        service = fresh_service(slot_count=3)

        results = [
            service.enter_vehicle(reg, 'CAR')
            for reg in ('KAA 111A', 'KBB 222B', 'KCC 333C')
        ]

        allocated = [r.slot.slot_number for r in results]
        self.assertEqual(allocated, ['T01', 'T02', 'T03'])
        self.assertEqual(len(set(allocated)), 3)

    def test_release_slot_makes_it_available_again(self):
        service = fresh_service(slot_count=2)

        result = service.enter_vehicle('KAA 111A', 'CAR')
        self.assertEqual(result.slot.slot_number, 'T01')
        self.assertFalse(
            service.slot_registry.get_slot('T01').is_available()
        )

        released = service.slot_registry.release_slot('T01')
        self.assertTrue(released)
        self.assertTrue(service.slot_registry.get_slot('T01').is_available())


# =============================================================================
# VEHICLE ENTRY
# =============================================================================

class VehicleEntryTests(TestCase):
    """Vehicle entry workflow: valid, invalid, duplicate, full."""

    def test_valid_vehicle_entry_creates_active_session(self):
        service = fresh_service()

        result = service.enter_vehicle('KAA 123A', 'CAR')

        self.assertTrue(result.success, result.message)
        self.assertIsNotNone(result.session)
        self.assertEqual(result.session.vehicle_id, 'KAA 123A')
        self.assertEqual(result.session.status, ds.SessionStatus.ACTIVE)
        self.assertIsNotNone(result.session.entry_time)
        self.assertIsNotNone(result.slot)

    def test_registration_number_is_normalized(self):
        """'kaa123a' must be stored as 'KAA 123A'."""
        service = fresh_service()

        result = service.enter_vehicle('kaa123a', 'CAR')

        self.assertTrue(result.success, result.message)
        self.assertEqual(result.session.vehicle_id, 'KAA 123A')

    def test_duplicate_active_vehicle_is_rejected(self):
        service = fresh_service()

        first = service.enter_vehicle('KAA 123A', 'CAR')
        self.assertTrue(first.success, first.message)

        second = service.enter_vehicle('KAA 123A', 'CAR')
        self.assertFalse(second.success)
        self.assertIn('already has an active session', second.message)

    def test_invalid_registration_rejected_by_django_validator(self):
        """Invalid Kenyan plate formats must fail model validation."""
        invalid_plates = ['INVALID', 'ABC 123', '12345', 'KAA-123A']
        for plate in invalid_plates:
            with self.subTest(plate=plate):
                vehicle = Vehicle(registration_number=plate, vehicle_type='CAR')
                with self.assertRaises(ValidationError):
                    vehicle.full_clean()

    def test_valid_registration_accepted_by_django_validator(self):
        vehicle = Vehicle(registration_number='KAA 123A', vehicle_type='CAR')
        vehicle.full_clean()  # must not raise

    def test_full_parking_puts_vehicle_in_waiting_queue(self):
        service = fresh_service(slot_count=1)

        first = service.enter_vehicle('KAA 111A', 'CAR')
        self.assertTrue(first.success)

        second = service.enter_vehicle('KBB 222B', 'CAR')
        self.assertFalse(second.success)
        self.assertTrue(second.queued)
        self.assertEqual(second.queue_position, 1)

        queue = service.waiting_queue.get_all()
        self.assertEqual(queue[0]['registration_number'], 'KBB 222B')


# =============================================================================
# VEHICLE EXIT
# =============================================================================

class VehicleExitTests(TestCase):
    """Exit workflow: duration, fee, payment required, not found."""

    def test_valid_exit_records_duration_and_fee(self):
        service = fresh_service()
        entry = service.enter_vehicle('KAA 123A', 'CAR')
        self.assertTrue(entry.success, entry.message)

        exit_time = entry.session.entry_time + timedelta(minutes=95)
        result = service.exit_vehicle('KAA 123A', exit_time.isoformat())

        self.assertTrue(result.success, result.message)
        self.assertEqual(result.duration_minutes, 95)
        # 95 minutes falls in the 31-120 minute tier -> KSh 50
        self.assertEqual(result.fee, 50)
        self.assertTrue(result.payment_required)
        self.assertFalse(result.exit_authorized)
        # Slot must NOT be released before payment
        self.assertEqual(
            service.slot_registry.get_slot('T01').status,
            ds.SlotStatus.OCCUPIED,
        )
        # Session stays ACTIVE until payment completes it
        self.assertEqual(
            service.session_index.get_by_vehicle('KAA 123A').status,
            ds.SessionStatus.ACTIVE,
        )

    def test_exit_with_exact_fee_boundaries(self):
        """Exit at 120 min -> KSh 50, at 241 min -> KSh 300."""
        service = fresh_service()

        first = service.enter_vehicle('KAA 123A', 'CAR')
        exit_120 = first.session.entry_time + timedelta(minutes=120)
        result = service.exit_vehicle('KAA 123A', exit_120.isoformat())
        self.assertEqual(result.duration_minutes, 120)
        self.assertEqual(result.fee, 50)

        # Pay so the session completes and the slot frees up
        service.process_payment(first.session.session_id, 50, 'CASH')

        second = service.enter_vehicle('KBB 222B', 'CAR')
        exit_241 = second.session.entry_time + timedelta(minutes=241)
        result_2 = service.exit_vehicle('KBB 222B', exit_241.isoformat())
        self.assertEqual(result_2.duration_minutes, 241)
        self.assertEqual(result_2.fee, 300)

    def test_free_parking_exit_completes_immediately(self):
        """30 minutes or less: no payment, exit authorized at once."""
        service = fresh_service()

        entry = service.enter_vehicle('KAA 123A', 'CAR')
        exit_time = entry.session.entry_time + timedelta(minutes=30)
        result = service.exit_vehicle('KAA 123A', exit_time.isoformat())

        self.assertTrue(result.success, result.message)
        self.assertEqual(result.fee, 0)
        self.assertFalse(result.payment_required)
        self.assertTrue(result.exit_authorized)
        # Slot released and session completed without payment
        self.assertTrue(service.slot_registry.get_slot('T01').is_available())
        self.assertEqual(
            service.session_index.get_by_vehicle('KAA 123A').status,
            ds.SessionStatus.COMPLETED,
        )

    def test_vehicle_without_active_session_cannot_exit(self):
        service = fresh_service()

        result = service.exit_vehicle('KZZ 999Z')

        self.assertFalse(result.success)
        self.assertIn('No active session', result.message)


# =============================================================================
# PAYMENT (CASH only for now)
# =============================================================================

class PaymentTests(TestCase):
    """Cash payment: success, wrong amount, disabled method, release."""

    def _parked_session(self, minutes: int = 90):
        """Helper: park a vehicle and exit it after `minutes` minutes."""
        service = fresh_service()
        entry = service.enter_vehicle('KAA 123A', 'CAR')
        exit_time = entry.session.entry_time + timedelta(minutes=minutes)
        exit_result = service.exit_vehicle('KAA 123A', exit_time.isoformat())
        self.assertTrue(exit_result.success, exit_result.message)
        return service, entry.session, exit_result

    def test_successful_cash_payment_authorizes_exit(self):
        service, session, exit_result = self._parked_session(90)

        result = service.process_payment(session.session_id, 50, 'CASH')

        self.assertTrue(result.success, result.message)
        self.assertTrue(result.exit_authorized)
        self.assertEqual(result.payment.payment_method, 'CASH')
        self.assertEqual(result.payment.status, ds.PaymentStatus.PAID)
        self.assertTrue(result.payment.transaction_reference)

    def test_payment_releases_slot_and_completes_session(self):
        service, session, _ = self._parked_session(90)

        service.process_payment(session.session_id, 50, 'CASH')

        self.assertTrue(service.slot_registry.get_slot('T01').is_available())
        self.assertEqual(
            service.session_index.get_session(session.session_id).status,
            ds.SessionStatus.COMPLETED,
        )

    def test_wrong_amount_is_rejected(self):
        """Amount is validated server-side against the calculated fee."""
        service, session, _ = self._parked_session(90)

        result = service.process_payment(session.session_id, 999, 'CASH')

        self.assertFalse(result.success)
        self.assertIn('Invalid amount', result.message)

    def test_non_cash_method_rejected_for_now(self):
        """M-Pesa/card will be configured later; only CASH works today."""
        service, session, _ = self._parked_session(90)

        result = service.process_payment(session.session_id, 50, 'MPESA')

        self.assertFalse(result.success)
        self.assertIn('Only CASH payment is currently enabled', result.message)

    def test_payment_before_exit_is_rejected(self):
        """No exit time recorded -> no amount due -> payment refused."""
        service = fresh_service()
        entry = service.enter_vehicle('KAA 123A', 'CAR')

        result = service.process_payment(entry.session.session_id, 0, 'CASH')

        self.assertFalse(result.success)
        self.assertIn('Exit has not been recorded', result.message)

    def test_double_payment_is_rejected(self):
        service, session, _ = self._parked_session(90)

        first = service.process_payment(session.session_id, 50, 'CASH')
        second = service.process_payment(session.session_id, 50, 'CASH')

        self.assertTrue(first.success)
        self.assertFalse(second.success)
        self.assertIn('already completed', second.message)


# =============================================================================
# INTEGRATION: full workflow + waiting queue
# =============================================================================

class IntegrationTests(TestCase):
    """Django -> Flask -> Supabase style workflow (in-process)."""

    def test_complete_workflow_entry_exit_payment(self):
        """entry -> exit (90 min, KSh 50) -> cash payment -> completed."""
        service = fresh_service()

        entry = service.enter_vehicle('KFG 678F', 'CAR')
        self.assertTrue(entry.success, entry.message)

        exit_time = entry.session.entry_time + timedelta(minutes=90)
        exit_result = service.exit_vehicle('KFG 678F', exit_time.isoformat())
        self.assertTrue(exit_result.success, exit_result.message)
        self.assertEqual(exit_result.fee, 50)

        payment = service.process_payment(
            entry.session.session_id, 50, 'CASH'
        )
        self.assertTrue(payment.success, payment.message)
        self.assertTrue(payment.exit_authorized)

        session = service.session_index.get_session(entry.session.session_id)
        self.assertEqual(session.status, ds.SessionStatus.COMPLETED)
        self.assertTrue(service.slot_registry.get_slot('T01').is_available())

    def test_waiting_vehicle_gets_slot_after_payment(self):
        """FIFO queue: waiting vehicle is allocated the freed slot."""
        service = fresh_service(slot_count=1)

        first = service.enter_vehicle('KAA 111A', 'CAR')
        queued = service.enter_vehicle('KBB 222B', 'CAR')
        self.assertTrue(queued.queued)
        self.assertEqual(service.waiting_queue.size(), 1)

        exit_time = first.session.entry_time + timedelta(minutes=45)
        service.exit_vehicle('KAA 111A', exit_time.isoformat())
        result = service.process_payment(first.session.session_id, 50, 'CASH')

        # Queue was served when the slot was released
        self.assertEqual(service.waiting_queue.size(), 0)
        self.assertIn('allocated slot', result.message)
        active = service.session_index.get_by_vehicle('KBB 222B')
        self.assertIsNotNone(active)
        self.assertEqual(active.status, ds.SessionStatus.ACTIVE)

    def test_django_vehicle_and_slot_models_persist(self):
        """ORM side: create vehicle + slot + session with FK integrity."""
        slot = ParkingSlot.objects.create(slot_number='A01', location='Block A')
        vehicle = Vehicle.objects.create(
            registration_number='KAA 123A', vehicle_type='CAR'
        )
        session = ParkingSession.objects.create(
            vehicle=vehicle, slot=slot, status=ParkingSession.Status.ACTIVE
        )

        self.assertEqual(str(vehicle), 'KAA 123A (CAR)')
        self.assertTrue(slot.is_available())
        self.assertTrue(session.is_active)
        self.assertEqual(session.vehicle_id, vehicle.id)

        # Payment FK chain: payment -> session -> vehicle
        payment = Payment.objects.create(
            session=session,
            amount=50,
            payment_method=Payment.PaymentMethod.CASH,
            payment_status=Payment.PaymentStatus.PAID,
            transaction_reference='CASH-TEST0001',
        )
        self.assertEqual(payment.session.vehicle.registration_number, 'KAA 123A')


# =============================================================================
# DATA STRUCTURES (DSA academic requirement)
# =============================================================================

class DataStructureTests(TestCase):
    """Documented data structures: dict lookup, FIFO queue, indexes."""

    def test_slot_registry_dictionary_lookup(self):
        registry = ds.ParkingSlotRegistry()
        for number in ('A01', 'A02', 'B01'):
            registry.add_slot(ds.ParkingSlot(slot_number=number))

        self.assertIn('A01', registry)
        self.assertEqual(len(registry), 3)
        # Direct dictionary access: O(1) average
        self.assertEqual(registry.get_slot('A01').slot_number, 'A01')
        self.assertIsNone(registry.get_slot('ZZZ'))

    def test_vehicle_registry_o1_lookup(self):
        registry = ds.VehicleRegistry()
        registry.register_vehicle(
            ds.Vehicle(registration_number='KAA 123A', vehicle_type='CAR')
        )

        self.assertTrue(registry.vehicle_exists('kaa123a'))
        self.assertEqual(
            registry.get_vehicle('kaa123a').registration_number, 'KAA 123A'
        )
        self.assertFalse(registry.vehicle_exists('KBB 222B'))

    def test_waiting_queue_is_fifo(self):
        queue = ds.WaitingQueue()
        queue.enqueue('KAA 111A', 'CAR')
        queue.enqueue('KBB 222B', 'CAR')
        queue.enqueue('KCC 333C', 'CAR')

        self.assertEqual(queue.size(), 3)
        self.assertEqual(queue.dequeue()['registration_number'], 'KAA 111A')
        self.assertEqual(queue.dequeue()['registration_number'], 'KBB 222B')
        self.assertEqual(queue.peek()['registration_number'], 'KCC 333C')
        self.assertEqual(queue.dequeue()['registration_number'], 'KCC 333C')
        self.assertTrue(queue.is_empty())
        self.assertIsNone(queue.dequeue())

    def test_session_index_bidirectional_lookup(self):
        index = ds.SessionIndex()
        session = ds.ParkingSession(
            session_id=1,
            vehicle_id='KAA 123A',
            slot_id='A01',
            entry_time=datetime(2026, 9, 25, 8, 0, 0),
        )
        index.add_session(session)

        self.assertEqual(index.get_session(1).session_id, 1)
        self.assertEqual(index.get_by_vehicle('KAA 123A').session_id, 1)
        self.assertEqual(index.get_by_slot('A01').session_id, 1)
        self.assertEqual(index.get_active_sessions(), [session])

        index.remove_session(1)
        self.assertEqual(len(index), 0)
        self.assertIsNone(index.get_by_vehicle('KAA 123A'))