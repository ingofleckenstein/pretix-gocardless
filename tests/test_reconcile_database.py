"""Transactional SQLite tests with small models, not pretix integration tests."""
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import django
from django.conf import settings

if not settings.configured:
    settings.configure(INSTALLED_APPS=[], DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}, SECRET_KEY="test-only", USE_TZ=True)
    django.setup()

from django.db import connection, models  # noqa: E402

from pretix_gocardless.reconcile import RequiresManualReview, reconcile  # noqa: E402


class TestPayment(models.Model):
    provider = models.CharField(max_length=30, default="gocardless")
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    state = models.CharField(max_length=30, default="pending")

    class Meta:
        app_label = "reconcile_test"

    @property
    def order(self):
        return SimpleNamespace(event=SimpleNamespace(currency="EUR"))

    def confirm(self):
        self.state = "confirmed"
        self.save(update_fields=["state"])

    def fail(self, **kwargs):
        self.state = "failed"
        self.save(update_fields=["state"])


class TestReference(models.Model):
    payment = models.ForeignKey(TestPayment, on_delete=models.CASCADE)
    environment = models.CharField(max_length=10)
    remote_payment_id = models.CharField(max_length=40)
    last_status = models.CharField(max_length=40, default="")
    reversal_recorded = models.BooleanField(default=False)

    class Meta:
        app_label = "reconcile_test"


class TestEvent(models.Model):
    event_id = models.CharField(max_length=64, unique=True)
    reference = models.ForeignKey(TestReference, on_delete=models.CASCADE)

    class Meta:
        app_label = "reconcile_test"


class DatabaseTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema:
            for model in (TestPayment, TestReference, TestEvent):
                schema.create_model(model)

    @classmethod
    def tearDownClass(cls):
        with connection.schema_editor() as schema:
            for model in (TestEvent, TestReference, TestPayment):
                schema.delete_model(model)
        super().tearDownClass()

    def setUp(self):
        TestEvent.objects.all().delete()
        TestReference.objects.all().delete()
        TestPayment.objects.all().delete()
        self.payment = TestPayment.objects.create(amount=Decimal("12.34"))
        self.ref = TestReference.objects.create(payment=self.payment, environment="sandbox", remote_payment_id="PM123")
        self.remote = {"id": "PM123", "amount": 1234, "currency": "EUR", "status": "confirmed"}
        local = ModuleType("pretix_gocardless.models")
        local.PaymentReference, local.ProcessedEvent = TestReference, TestEvent
        pretend = ModuleType("pretix.base.models")
        pretend.OrderPayment = TestPayment
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
