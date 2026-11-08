"""
Regression tests for the security review fixes.

Covers: authentication on every page, client-controlled exit times,
input validation on the engine API, error-message leakage, the optional
engine API key, waiting-queue fairness, concurrency, report parameter
bounds and CSV formula injection.

Run from the project root:
    .venv\\Scripts\\python.exe parkflow\\manage.py test tests -v 2
"""
import json
import logging
import os
import sys
import threading
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, 'parkflow')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from flask_api import data_structures as ds
from flask_api.config import Config, TestingConfig
from flask_api.services import init_parking_service
from parking.models import ParkingSession, ParkingSlot
from payments.models import Payment
from vehicles.models import Vehicle


def fresh_service(slot_count=3):
    service = init_parking_service([], [], [])
    for i in range(1, slot_count + 1):
        service.slot_registry.add_slot(ds.ParkingSlot(slot_number=f'T{i:02d}'))
    return service


class _OfflineSupabase:
    """Stand-in so create_app() never touches the real Supabase project."""
    client = None

    def is_connected(self):
        return False


def make_flask_client(config_class=Config, slot_count=3):
    """A Flask test client over a fresh in-memory service (no database)."""
    service = fresh_service(slot_count)
    offline = _OfflineSupabase()
    logging.disable(logging.WARNING)  # expected offline / no-API-key warnings
    try:
        with mock.patch('flask_api.app.get_supabase_service', return_value=offline), \
                mock.patch('flask_api.services.get_supabase_service', return_value=offline):
            from flask_api.app import create_app
            app = create_app(config_class)
    finally:
        logging.disable(logging.NOTSET)
    return app.test_client(), service


# =============================================================================
# Flask engine API
# =============================================================================

class EngineApiValidationTests(TestCase):

    def setUp(self):
        self.client_api, self.service = make_flask_client()

    def _post(self, path, payload, **kwargs):
        return self.client_api.post(f'/api/v1{path}', json=payload, **kwargs)

    def test_client_cannot_backdate_exit_time(self):
        """A client-chosen exit_time would allow free parking - refused."""
        self._post('/parking/entry', {'registration_number': 'KAA 123A'})
        response = self._post('/parking/exit', {
            'registration_number': 'KAA 123A',
            'exit_time': '2000-01-01T00:00:00',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('exit_time', response.get_json()['message'])
        # Session untouched: still active, no exit recorded.
        session = self.service.session_index.get_by_vehicle('KAA 123A')
        self.assertIsNone(session.exit_time)

    def test_markup_in_registration_is_rejected(self):
        response = self._post('/parking/entry', {
            'registration_number': '<img src=x onerror=alert(1)>',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.service.vehicle_registry), 0)

    def test_unknown_vehicle_type_is_rejected(self):
        response = self._post('/parking/entry', {
            'registration_number': 'KAA 123A', 'vehicle_type': 'SPACESHIP',
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self.service.session_index), 0)

    def test_non_object_json_body_is_400(self):
        response = self._post('/parking/entry', ['KAA 123A'])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_unexpected_errors_do_not_leak_details(self):
        with mock.patch.object(
            self.service, 'get_availability',
            side_effect=RuntimeError('secret connection string'),
        ), self.assertLogs('flask_api.app', level='ERROR'):
            response = self.client_api.get('/api/v1/availability')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('secret', response.get_data(as_text=True))

    def test_numeric_string_amount_is_accepted_bool_is_not(self):
        override_client, service = make_flask_client(TestingConfig)
        entry = override_client.post('/api/v1/parking/entry', json={
            'registration_number': 'KAA 123A'}).get_json()['data']
        exit_at = (service.session_index.get_session(entry['session_id']).entry_time
                   + timedelta(minutes=90)).isoformat()
        override_client.post('/api/v1/parking/exit', json={
            'registration_number': 'KAA 123A', 'exit_time': exit_at})

        as_bool = override_client.post('/api/v1/payments/process', json={
            'session_id': entry['session_id'], 'amount': True})
        self.assertEqual(as_bool.status_code, 400)

        as_text = override_client.post('/api/v1/payments/process', json={
            'session_id': entry['session_id'], 'amount': '50'})
        self.assertEqual(as_text.status_code, 200, as_text.get_json())


class EngineApiKeyTests(TestCase):

    def test_api_key_is_enforced_when_configured(self):
        class KeyedConfig(Config):
            API_KEY = 'test-shared-secret'

        client_api, _ = make_flask_client(KeyedConfig)

        self.assertEqual(client_api.get('/api/v1/availability').status_code, 401)
        self.assertEqual(
            client_api.get('/api/v1/availability',
                           headers={'X-API-Key': 'wrong'}).status_code, 401)
        self.assertEqual(
            client_api.get('/api/v1/availability',
                           headers={'X-API-Key': 'test-shared-secret'}).status_code, 200)
        # Health check stays open for monitoring.
        self.assertEqual(client_api.get('/api/v1/status').status_code, 200)


# =============================================================================
# Engine workflow fixes
# =============================================================================

class WorkflowFixTests(TestCase):

    def test_exit_before_entry_is_rejected(self):
        service = fresh_service()
        entry = service.enter_vehicle('KAA 123A', 'CAR')
        result = service.exit_vehicle(
            'KAA 123A', entry.session.entry_time - timedelta(hours=1)
        )
        self.assertFalse(result.success)
        self.assertIn('earlier than the entry', result.message)

    def test_retrying_entry_does_not_duplicate_queue(self):
        service = fresh_service(slot_count=1)
        service.enter_vehicle('KAA 111A', 'CAR')

        first = service.enter_vehicle('KBB 222B', 'CAR')
        retry = service.enter_vehicle('KBB 222B', 'CAR')
        other = service.enter_vehicle('KCC 333C', 'CAR')

        self.assertEqual(service.waiting_queue.size(), 2)
        self.assertEqual((first.queue_position, retry.queue_position), (1, 1))
        self.assertEqual(other.queue_position, 2)

    def test_concurrent_duplicate_entries_create_one_session(self):
        service = fresh_service(slot_count=10)
        barrier = threading.Barrier(8)
        results = []

        def enter():
            barrier.wait()
            results.append(service.enter_vehicle('KAA 123A', 'CAR'))

        threads = [threading.Thread(target=enter) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(r.success for r in results), 1)
        self.assertEqual(len(service.session_index.get_active_sessions()), 1)
        self.assertEqual(len(service.slot_registry.get_available_slots()), 9)

    def test_engine_timestamps_are_timezone_aware(self):
        service = fresh_service()
        entry = service.enter_vehicle('KAA 123A', 'CAR')
        self.assertIsNotNone(entry.session.entry_time.tzinfo)


# =============================================================================
# Django web app
# =============================================================================

class _LoggedInTestCase(TestCase):

    def setUp(self):
        user = get_user_model().objects.create_user('operator', password='pw-test-123')
        self.client.force_login(user)

    def post_json(self, path, payload):
        return self.client.post(
            path, data=json.dumps(payload), content_type='application/json'
        )


class AuthenticationTests(TestCase):

    def test_pages_require_login(self):
        for path in ('/dashboard/', '/payments/', '/vehicles/', '/reports/',
                     '/reports/daily-summary/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/accounts/login/', response['Location'])

    def test_workflow_endpoints_require_login(self):
        response = self.client.post(
            '/payments/process/',
            data=json.dumps({'session_id': 1, 'amount': 50}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 302)

    def test_login_page_is_public(self):
        self.assertEqual(self.client.get('/accounts/login/').status_code, 200)


class ProxyInputTests(_LoggedInTestCase):

    def test_json_array_body_is_400_not_500(self):
        response = self.post_json('/parking/entry/', ['KAA 123A'])
        self.assertEqual(response.status_code, 400)

    def test_non_string_registration_is_400(self):
        response = self.post_json('/parking/entry/', {'registration_number': 123})
        self.assertEqual(response.status_code, 400)

    def test_bad_session_id_in_fee_preview_is_400(self):
        response = self.client.get('/payments/calculate-fee/?session_id=abc')
        self.assertEqual(response.status_code, 400)

    @override_settings(ALLOW_EXIT_TIME_OVERRIDE=False)
    def test_exit_proxy_does_not_forward_client_exit_time(self):
        with mock.patch('common.flask_client.request',
                        return_value=({'success': False, 'message': 'x'}, 404)) as req:
            self.post_json('/parking/exit/', {
                'registration_number': 'KAA 123A',
                'exit_time': '2000-01-01T00:00:00',
            })
        payload = req.call_args.args[2]
        self.assertNotIn('exit_time', payload)

    def test_payment_proxy_does_not_forward_client_reference(self):
        with mock.patch('common.flask_client.request',
                        return_value=({'success': False, 'message': 'x'}, 400)) as req:
            self.post_json('/payments/process/', {
                'session_id': 1, 'amount': 50,
                'transaction_reference': 'x' * 500,
            })
        self.assertNotIn('transaction_reference', req.call_args.args[2])


class PageFixTests(_LoggedInTestCase):

    def _session(self, status):
        vehicle = Vehicle.objects.create(registration_number='KAA 123A')
        slot = ParkingSlot.objects.create(slot_number='A01')
        return ParkingSession.objects.create(vehicle=vehicle, slot=slot, status=status)

    def test_dashboard_pages_render_while_engine_is_down(self):
        from common.flask_client import FlaskAPIError
        self._session(ParkingSession.Status.ACTIVE)
        with mock.patch('common.flask_client.request', side_effect=FlaskAPIError('down')), \
                self.assertLogs('common', level='WARNING'):
            for path in ('/dashboard/', '/parking/', '/dashboard/data/'):
                with self.subTest(path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
            dashboard = self.client.get('/dashboard/').content.decode()
        self.assertIn('KAA 123A', dashboard)
        self.assertNotRegex(dashboard, r'\.innerHTML\s*=')

    def test_vehicle_list_shows_active_session_badge(self):
        """The old template expression always rendered 'No'."""
        self._session(ParkingSession.Status.ACTIVE)
        response = self.client.get('/vehicles/')
        self.assertContains(response, '<span class="badge badge-active">Yes</span>', html=True)

    def test_vehicle_list_search(self):
        self._session(ParkingSession.Status.ACTIVE)
        Vehicle.objects.create(registration_number='KZZ 999Z')
        response = self.client.get('/vehicles/?q=kaa123')
        self.assertContains(response, 'KAA 123A')
        self.assertNotContains(response, 'KZZ 999Z')

    def test_report_days_parameter_is_bounded(self):
        response = self.client.get('/reports/revenue/?days=99999999999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['period_days'], 365)

    def test_vehicle_history_total_paid_counts_payments_only(self):
        session = self._session(ParkingSession.Status.ACTIVE)
        session.amount_due = 300  # exit recorded, not yet paid
        session.save()
        response = self.client.get('/reports/vehicle-history/?registration_number=KAA123A')
        self.assertEqual(response.json()['data']['total_paid'], 0)

    def test_csv_export_neutralises_formulas(self):
        from reports.views import _SafeCSVWriter
        import io

        buffer = io.StringIO()
        _SafeCSVWriter(buffer).writerow(['=HYPERLINK("http://x")', 'KAA 123A', 50])
        self.assertTrue(buffer.getvalue().startswith('"\'=HYPERLINK'))
        self.assertIn('KAA 123A,50', buffer.getvalue())

    def test_report_log_records_operator(self):
        from reports.models import ReportLog
        self.client.get('/reports/daily-summary/')
        self.assertEqual(ReportLog.objects.latest('generated_at').generated_by, 'operator')

    def test_payment_history_total_ignores_refunds(self):
        session = self._session(ParkingSession.Status.COMPLETED)
        Payment.objects.create(session=session, amount=50, transaction_reference='A',
                               payment_status=Payment.PaymentStatus.PAID)
        Payment.objects.create(session=session, amount=50, transaction_reference='B',
                               payment_status=Payment.PaymentStatus.REFUNDED)
        response = self.client.get(f'/payments/session/{session.id}/history/')
        self.assertEqual(response.json()['data']['total_paid'], 50)
