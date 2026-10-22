"""Mandate-event service contract tests with fake storage."""
from contextlib import nullcontext
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from pretix_gocardless.events import EventReference
from pretix_gocardless.mandates import process_mandate
from pretix_gocardless.reconcile import ReconciliationError


class MandateEventTests(TestCase):
    def setUp(self):
        self.organizer = SimpleNamespace(pk=12)
        self.event = SimpleNamespace(organizer=self.organizer, organizer_id=12, currency="EUR")
        self.order = SimpleNamespace(event=self.event, log_action=MagicMock())
        self.remote_payment = SimpleNamespace(
            pk=7, provider="gocardless", amount=Decimal("12.34"), state="pending",
            local_id=1, order=self.order, fail=MagicMock(),
        )
        self.no_remote_payment = SimpleNamespace(
            pk=8, provider="gocardless", amount=Decimal("9.00"), state="pending",
            local_id=2, order=self.order, fail=MagicMock(),
        )
        self.first = SimpleNamespace(
            pk=1, payment_id=7, environment="sandbox", creditor_id="CR123",
            mandate_id="MD123", mandate_status="active", remote_payment_id="PM123",
            released_pending_tickets=False, save=MagicMock(),
        )
        self.second = SimpleNamespace(
            pk=2, payment_id=8, environment="sandbox", creditor_id="CR123",
            mandate_id="MD123", mandate_status="active", remote_payment_id=None,
            released_pending_tickets=False, save=MagicMock(),
        )
        self.references = MagicMock()
        query = self.references.objects.select_for_update.return_value.select_related.return_value
        query.filter.return_value.order_by.return_value = [self.first, self.second]
        self.events = MagicMock()
        self.events.objects.filter.return_value.first.return_value = None
        self.payments = MagicMock()
        payment_map = {7: self.remote_payment, 8: self.no_remote_payment}
        self.payments.objects.select_for_update.return_value.get.side_effect = (
            lambda **kwargs: payment_map[kwargs["pk"]]
        )
        self.models = ModuleType("pretix_gocardless.models")
        self.models.PaymentReference = self.references
        self.models.ProcessedEvent = self.events
        self.pretix = ModuleType("pretix.base.models")
        self.pretix.OrderPayment = self.payments
        self.client = Mock()
        self.client.mandate.return_value = {
            "id": "MD123", "scheme": "sepa_core", "status": "cancelled",
            "links": {"creditor": "CR123"},
        }
        self.client.payment.return_value = {
            "id": "PM123", "amount": 1234, "currency": "EUR",
            "status": "pending_submission", "links": {"mandate": "MD123"},
        }
        self.reference = EventReference("EV123", "mandates", "MD123", action="cancelled")

    def run_service(self):
        with patch.dict("sys.modules", {
                "pretix_gocardless.models": self.models, "pretix.base.models": self.pretix,
             }), patch("pretix_gocardless.mandates.transaction.atomic", side_effect=nullcontext), \
                patch("pretix_gocardless.mandates.apply_status", return_value="wait") as apply:
            result = process_mandate(self.reference, self.event, "sandbox", self.client)
        return result, apply

    def test_cancelled_mandate_reconciles_remote_payment_and_fails_unstarted_one(self):
        result, apply = self.run_service()
        self.assertEqual(result, "mandate_cancelled:wait,fail")
        self.assertEqual((self.first.mandate_status, self.second.mandate_status), ("cancelled", "cancelled"))
        apply.assert_called_once_with(self.first, self.remote_payment, "pending_submission")
        self.no_remote_payment.fail.assert_called_once_with(
            log_data={"gocardless_mandate_status": "cancelled"})
        self.order.log_action.assert_called_once_with(
            "pretix_gocardless.event.order.mandate_inactive",
            {"local_id": 2, "gocardless_mandate_status": "cancelled"},
        )
        self.events.objects.create.assert_called_once_with(event_id="EV123", reference=self.first)

    def test_duplicate_is_acknowledged_without_remote_reads(self):
        self.events.objects.filter.return_value.first.return_value = SimpleNamespace(reference_id=1)
        result, apply = self.run_service()
        self.assertEqual(result, "duplicate")
        self.client.mandate.assert_not_called()
        apply.assert_not_called()

    def test_replaced_mandate_is_retried_until_atomic_rebinding_exists(self):
        self.reference = EventReference("EV123", "mandates", "MD123", action="replaced")
        with self.assertRaises(ReconciliationError):
            self.run_service()

    def test_event_and_current_status_must_agree(self):
        self.client.mandate.return_value["status"] = "active"
        with self.assertRaises(ReconciliationError):
            self.run_service()
