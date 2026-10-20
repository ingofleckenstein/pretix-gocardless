"""Transactional SQLite tests with small models, not pretix integration tests."""
from decimal import Decimal
import uuid
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import django
from django.conf import settings

if not settings.configured:
    settings.configure(INSTALLED_APPS=[], DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}, SECRET_KEY="test-only", USE_TZ=True)
    django.setup()

from django.db import connection, models  # noqa: E402

from pretix_gocardless.reconcile import ReconciliationError, RequiresManualReview, reconcile  # noqa: E402
from pretix_gocardless.billing import process_billing  # noqa: E402
from pretix_gocardless.client import GoCardlessError, IdempotencyConflict  # noqa: E402
from pretix_gocardless.checkout import start_authorisation, CheckoutMismatch  # noqa: E402


class TestPayment(models.Model):
    provider = models.CharField(max_length=30, default="gocardless")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    state = models.CharField(max_length=30, default="pending")

    class Meta:
        app_label = "reconcile_test"

    @property
    def order(self):
        return SimpleNamespace(event=SimpleNamespace(currency="EUR"), customer_id=1)

    def confirm(self):
        self.state = "confirmed"
        self.save(update_fields=["state"])

    def fail(self, **kwargs):
        self.state = "failed"
        self.save(update_fields=["state"])


class TestReference(models.Model):
    payment = models.ForeignKey(TestPayment, on_delete=models.CASCADE)
    environment = models.CharField(max_length=10)
    remote_payment_id = models.CharField(max_length=40, null=True)
    mandate_id = models.CharField(max_length=40, null=True, default="MD123")
    creditor_id = models.CharField(max_length=40, default="CR123")
    billing_request_id = models.CharField(max_length=40, default="BRQ123")
    creation_key = models.UUIDField(default=uuid.uuid4)
    last_status = models.CharField(max_length=40, default="")
    reversal_recorded = models.BooleanField(default=False)
    released_pending_tickets = models.BooleanField(default=False)

    class Meta:
        app_label = "reconcile_test"


class TestEvent(models.Model):
    event_id = models.CharField(max_length=64, unique=True)
    reference = models.ForeignKey(TestReference, on_delete=models.CASCADE)

    class Meta:
        app_label = "reconcile_test"


class TestCustomer(models.Model):
    class Meta:
        app_label = "reconcile_test"


class TestCustomerLink(models.Model):
    customer_id = models.IntegerField()
    environment = models.CharField(max_length=10)
    creditor_id = models.CharField(max_length=64)
    remote_customer_id = models.CharField(max_length=64)

    class Meta:
        app_label = "reconcile_test"
        unique_together = (("customer_id", "environment", "creditor_id"),)


class DatabaseTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema:
            for model in (TestPayment, TestReference, TestEvent, TestCustomer, TestCustomerLink):
                schema.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            for model in (TestCustomerLink, TestCustomer, TestEvent, TestReference, TestPayment):
                schema.delete_model(model)
        super().tearDownClass()

    def setUp(self):
        TestEvent.objects.all().delete()
        TestReference.objects.all().delete()
        TestPayment.objects.all().delete()
        TestCustomerLink.objects.all().delete()
        TestCustomer.objects.all().delete()
        TestCustomer.objects.create(pk=1)
        self.payment = TestPayment.objects.create(amount=Decimal("12.34"))
        self.ref = TestReference.objects.create(payment=self.payment, environment="sandbox", remote_payment_id="PM123")
        self.remote = {"id": "PM123", "amount": 1234, "currency": "EUR", "status": "confirmed"}
        local = ModuleType("pretix_gocardless.models")
        local.PaymentReference, local.ProcessedEvent, local.CustomerLink = TestReference, TestEvent, TestCustomerLink
        pretend = ModuleType("pretix.base.models")
        pretend.OrderPayment = TestPayment
        pretend.Customer = TestCustomer
        self.modules = patch.dict("sys.modules", {"pretix_gocardless.models": local, "pretix.base.models": pretend})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def test_duplicate_event_changes_payment_once(self):
        self.assertEqual(reconcile(self.ref.pk, "EV123", "sandbox", self.remote), "confirm")
        self.assertEqual(reconcile(self.ref.pk, "EV123", "sandbox", self.remote), "duplicate")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.state, "confirmed")
        self.assertEqual(TestEvent.objects.count(), 1)

    def test_failed_event_write_rolls_back_payment(self):
        with patch.object(TestEvent.objects, "create", side_effect=RuntimeError("storage failure")):
            with self.assertRaises(RuntimeError):
                reconcile(self.ref.pk, "EV123", "sandbox", self.remote)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.state, "pending")
        self.assertEqual(TestEvent.objects.count(), 0)

    def test_chargeback_does_not_record_event(self):
        self.payment.state = "confirmed"
        self.payment.save()
        self.remote["status"] = "charged_back"
        with self.assertRaises(RequiresManualReview):
            reconcile(self.ref.pk, "EV123", "sandbox", self.remote)
        self.assertEqual(TestEvent.objects.count(), 0)

    def test_account_binding_is_checked_under_lock(self):
        self.remote["links"] = {"mandate": "MD123"}
        with self.assertRaises(ReconciliationError):
            reconcile(self.ref.pk, "EV123", "sandbox", self.remote, mandate_id="MD999", creditor_id="CR123")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.state, "pending")
        self.assertEqual(TestEvent.objects.count(), 0)
        self.assertEqual(reconcile(self.ref.pk, "EV123", "sandbox", self.remote, mandate_id="MD123", creditor_id="CR123"), "confirm")

    def test_billing_transaction_stores_ids_and_deduplicates(self):
        self.ref.remote_payment_id = None
        self.ref.mandate_id = None
        self.ref.save()
        client = Mock()
        remote = {**self.remote, "status": "pending_submission", "links": {"mandate": "MD123"}}
        client.payment.return_value = remote
        with patch("pretix_gocardless.billing.ensure_debit", return_value=("MD123", "PM123", "pending_submission")) as ensure:
            self.assertEqual(process_billing(self.ref.pk, "EV123", "sandbox", client), "wait")
            self.assertEqual(process_billing(self.ref.pk, "EV123", "sandbox", client), "duplicate")
            ensure.assert_called_once()
        self.ref.refresh_from_db()
        self.assertEqual((self.ref.mandate_id, self.ref.remote_payment_id), ("MD123", "PM123"))
        self.assertEqual(TestEvent.objects.count(), 1)

    def test_billing_failure_rolls_back_local_ids_and_event(self):
        self.ref.remote_payment_id = None
        self.ref.mandate_id = None
        self.ref.save()
        client = Mock()
        client.payment.return_value = {**self.remote, "amount": 999, "links": {"mandate": "MD123"}}
        with patch("pretix_gocardless.billing.ensure_debit", return_value=("MD123", "PM123", "pending_submission")):
            with self.assertRaises(ValueError):
                process_billing(self.ref.pk, "EV123", "sandbox", client)
        self.ref.refresh_from_db()
        self.assertIsNone(self.ref.remote_payment_id)
        self.assertIsNone(self.ref.mandate_id)
        self.assertEqual(TestEvent.objects.count(), 0)

    def test_billing_never_creates_for_wrong_environment_or_canceled_payment(self):
        self.ref.remote_payment_id = None
        self.ref.save(update_fields=["remote_payment_id"])
        client = Mock()
        with patch("pretix_gocardless.billing.ensure_debit") as ensure:
            with self.assertRaises(ReconciliationError):
                process_billing(self.ref.pk, "EV123", "live", client)
            self.payment.state = "canceled"
            self.payment.save()
            with self.assertRaises(ReconciliationError):
                process_billing(self.ref.pk, "EV123", "sandbox", client)
            ensure.assert_not_called()

    def test_timeout_then_conflict_recovers_without_new_payment(self):
        self.ref.remote_payment_id = None
        self.ref.mandate_id = None
        self.ref.save()
        client = Mock()
        client.billing_request.return_value = {
            "id": "BRQ123", "status": "fulfilled", "links": {"creditor": "CR123"},
            "mandate_request": {"scheme": "sepa_core", "currency": "EUR", "links": {"mandate": "MD123"}},
        }
        client.mandate.return_value = {"id": "MD123", "status": "submitted", "scheme": "sepa_core", "links": {"creditor": "CR123"}}
        client.create_debit_payment.side_effect = [GoCardlessError("timeout"), IdempotencyConflict("PM123")]
        client.payment.return_value = {**self.remote, "status": "pending_submission", "links": {"mandate": "MD123"}}
        with self.assertRaises(GoCardlessError):
            process_billing(self.ref.pk, "EV123", "sandbox", client)
        self.ref.refresh_from_db()
        self.assertIsNone(self.ref.remote_payment_id)
        self.assertEqual(TestEvent.objects.count(), 0)
        self.assertEqual(process_billing(self.ref.pk, "EV123", "sandbox", client), "wait")
        keys = [call.args[2] for call in client.create_debit_payment.call_args_list]
        self.assertEqual(keys[0], keys[1])
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.remote_payment_id, "PM123")
        self.assertEqual(TestEvent.objects.count(), 1)

    def test_checkout_persists_billing_before_hosted_flow(self):
        self.ref.billing_request_id = ""
        self.ref.remote_payment_id = None
        self.ref.save()
        client = Mock()
        client.create_mandate_request.return_value = {"id": "BRQ123"}
        client.billing_request.return_value = {"id": "BRQ123", "status": "pending", "links": {"creditor": "CR123", "customer": "CU123"},
            "mandate_request": {"scheme": "sepa_core", "currency": "EUR"}}
        target = "https://example.org/return"
        def flow(billing_id, return_url, key):
            self.ref.refresh_from_db()
            self.assertEqual(self.ref.billing_request_id, "BRQ123")
            return {"links": {"billing_request": billing_id}, "redirect_uri": target, "exit_uri": target,
                    "auto_fulfil": True, "lock_currency": True, "authorisation_url": "https://pay-sandbox.gocardless.com/test"}
        client.create_flow.side_effect = flow
        client.validate_flow_url.side_effect = lambda url: url
        self.assertEqual(start_authorisation(self.payment, client, "sandbox", "CR123", target), "https://pay-sandbox.gocardless.com/test")
        self.assertEqual(TestCustomerLink.objects.get().remote_customer_id, "CU123")

    def test_checkout_reuses_customer_and_rejects_mismatch(self):
        self.ref.billing_request_id = ""
        self.ref.remote_payment_id = None
        self.ref.save()
        TestCustomerLink.objects.create(customer_id=1, environment="sandbox", creditor_id="CR123", remote_customer_id="CU123")
        client = Mock()
        client.create_mandate_request.return_value = {"id": "BRQ123"}
        client.billing_request.return_value = {"id": "BRQ123", "status": "pending", "links": {"creditor": "CR123", "customer": "CU999"},
            "mandate_request": {"scheme": "sepa_core", "currency": "EUR"}}
        with self.assertRaises(CheckoutMismatch):
            start_authorisation(self.payment, client, "sandbox", "CR123", "https://example.org/return")
        self.assertEqual(client.create_mandate_request.call_args.kwargs["customer_id"], "CU123")

    def test_checkout_rejects_changed_environment(self):
        with self.assertRaises(CheckoutMismatch):
            start_authorisation(self.payment, Mock(), "live", "CR123", "https://example.org/return")

