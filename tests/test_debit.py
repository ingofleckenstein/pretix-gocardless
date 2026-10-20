from unittest import TestCase
from unittest.mock import Mock
from uuid import uuid4

from pretix_gocardless.client import IdempotencyConflict
from pretix_gocardless.debit import MandateNotReady, ensure_debit
from pretix_gocardless.validation import PaymentMismatch


class DebitTests(TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.billing_request.return_value = {
            "id": "BRQ123", "status": "fulfilled", "links": {"creditor": "CR123"},
            "mandate_request": {"currency": "EUR", "scheme": "sepa_core", "links": {"mandate": "MD123"}},
        }
        self.client.mandate.return_value = {"id": "MD123", "status": "active", "scheme": "sepa_core", "links": {"creditor": "CR123"}}
        self.client.create_debit_payment.return_value = {"id": "PM123"}
        self.client.payment.return_value = {"id": "PM123", "status": "pending_submission", "amount": 1234, "currency": "EUR", "links": {"mandate": "MD123"}}
        self.args = {"billing_request_id": "BRQ123", "creditor_id": "CR123", "amount": "12.34", "creation_key": uuid4()}

    def test_create_then_validate_remote_payment(self):
        self.assertEqual(ensure_debit(self.client, **self.args), ("MD123", "PM123", "pending_submission"))
        self.client.create_debit_payment.assert_called_once()
        self.client.payment.assert_called_once_with("PM123")

    def test_conflict_recovers_existing_payment_and_reuses_key(self):
        self.client.create_debit_payment.side_effect = IdempotencyConflict("PM123")
        ensure_debit(self.client, **self.args)
        first_key = self.client.create_debit_payment.call_args.args[2]
        ensure_debit(self.client, **self.args)
        self.assertEqual(first_key, self.client.create_debit_payment.call_args.args[2])

    def test_known_payment_is_validated_without_creating_again(self):
        self.client.mandate.return_value["status"] = "cancelled"
        ensure_debit(self.client, **self.args, known_mandate_id="MD123", known_payment_id="PM123")
        self.client.create_debit_payment.assert_not_called()

    def test_invalid_or_inactive_mandate_never_creates_payment(self):
        self.client.mandate.return_value["status"] = "failed"
        with self.assertRaises(MandateNotReady):
            ensure_debit(self.client, **self.args)
        self.client.create_debit_payment.assert_not_called()
        self.client.mandate.return_value["status"] = "active"
        with self.assertRaises(ValueError):
            ensure_debit(self.client, **self.args, known_mandate_id="MD999")
        self.client.create_debit_payment.assert_not_called()

    def test_pending_and_submitted_mandates_can_be_used(self):
        for status in ("pending_submission", "submitted"):
            with self.subTest(status=status):
                self.client.mandate.return_value["status"] = status
                self.assertEqual(ensure_debit(self.client, **self.args)[1], "PM123")

    def test_mismatched_recovered_payment_is_not_accepted(self):
        self.client.create_debit_payment.side_effect = IdempotencyConflict("PM123")
        self.client.payment.return_value["amount"] = 999
        with self.assertRaises(PaymentMismatch):
            ensure_debit(self.client, **self.args)
