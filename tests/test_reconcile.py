"""Service contract tests with fake storage; not database concurrency tests."""
from contextlib import nullcontext
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch
from pretix_gocardless.reconcile import RequiresManualReview, ReconciliationError, reconcile


class ReconcileTests(TestCase):
    def setUp(self):
        self.payment = SimpleNamespace(
            provider="gocardless", amount=Decimal("12.34"), state="pending",
            order=SimpleNamespace(event=SimpleNamespace(currency="EUR")),
            confirm=MagicMock(), fail=MagicMock(),
        )
        self.ref = SimpleNamespace(
            pk=1, payment_id=7, environment="sandbox", remote_payment_id="PM123",
            reversal_recorded=False, save=MagicMock(),
        )
        self.events = MagicMock()
        self.events.objects.filter.return_value.first.return_value = None
        self.ref_model = MagicMock()
        self.ref_model.objects.select_for_update.return_value.get.return_value = self.ref
        self.payment_model = MagicMock()
        self.payment_model.objects.select_for_update.return_value.get.return_value = self.payment
        self.model_module = ModuleType("pretix_gocardless.models")
        self.model_module.PaymentReference = self.ref_model
        self.model_module.ProcessedEvent = self.events
        self.pretix_module = ModuleType("pretix.base.models")
        self.pretix_module.OrderPayment = self.payment_model
        self.remote = {"id": "PM123", "amount": 1234, "currency": "EUR", "status": "confirmed"}

    def run_service(self, event="EV123", environment="sandbox"):
        with patch.dict("sys.modules", {"pretix_gocardless.models": self.model_module, "pretix.base.models": self.pretix_module}), \
             patch("pretix_gocardless.reconcile.transaction.atomic", side_effect=nullcontext):
            return reconcile(1, event, environment, self.remote)

    def test_confirmation_records_event(self):
        self.assertEqual(self.run_service(), "confirm")
        self.payment.confirm.assert_called_once()
        self.events.objects.create.assert_called_once_with(event_id="EV123", reference=self.ref)

    def test_duplicate_does_not_apply_again(self):
        self.events.objects.filter.return_value.first.return_value = SimpleNamespace(reference_id=1)
        self.assertEqual(self.run_service(), "duplicate")
        self.payment.confirm.assert_not_called()
        self.events.objects.create.assert_not_called()

    def test_foreign_duplicate_is_rejected(self):
        self.events.objects.filter.return_value.first.return_value = SimpleNamespace(reference_id=2)
        with self.assertRaises(ReconciliationError):
            self.run_service()

    def test_wrong_environment_or_amount_is_rejected(self):
        with self.assertRaises(ReconciliationError):
            self.run_service(environment="live")
        self.remote["amount"] = 999
        with self.assertRaises(ValueError):
            self.run_service()
        self.events.objects.create.assert_not_called()

    def test_chargeback_is_not_silently_acknowledged(self):
        self.payment.state = "confirmed"
        self.remote["status"] = "charged_back"
        with self.assertRaises(RequiresManualReview):
            self.run_service()
        self.events.objects.create.assert_not_called()
        self.payment.fail.assert_not_called()
