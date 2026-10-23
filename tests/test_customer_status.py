from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from pretix_gocardless.customer_status import CustomerStatus, assess
from pretix_gocardless.validation import PaymentMismatch


class CustomerStatusTests(TestCase):
    def setUp(self):
        self.ref = SimpleNamespace(environment="sandbox", creditor_id="CR123", billing_request_id="BRQ123",
                                   remote_payment_id=None, mandate_id="MD123")
        self.payment = SimpleNamespace(amount=Decimal("20.00"))
        self.billing = {"id": "BRQ123", "status": "pending", "links": {"creditor": "CR123"},
                        "mandate_request": {"scheme": "sepa_core", "currency": "EUR"}}
        self.client = Mock()
        self.client.billing_request.return_value = self.billing

    def test_unfinished_mandate_can_resume(self):
        self.assertEqual(assess(self.ref, self.payment, self.client), CustomerStatus.RESUME)

    def test_fulfilled_mandate_never_offers_second_authorisation(self):
        self.billing["status"] = "fulfilled"
        self.assertEqual(assess(self.ref, self.payment, self.client), CustomerStatus.PROCESSING)

    def test_mismatch_or_unknown_state_fails_closed(self):
        for key, value in (("id", "BRQOTHER"), ("links", {"creditor": "CROTHER"}),
                           ("status", "cancelled"), ("payment_request", {"amount": 2000})):
            old = self.billing.get(key)
            self.billing[key] = value
            self.assertEqual(assess(self.ref, self.payment, self.client), CustomerStatus.UNKNOWN)
            self.billing[key] = old

    def test_remote_payment_is_validated_and_never_offers_resume(self):
        self.ref.remote_payment_id = "PM123"
        remote = {"id": "PM123", "amount": 2000, "currency": "EUR", "links": {"mandate": "MD123"}}
        self.client.payment.return_value = remote
        for status, expected in (("submitted", CustomerStatus.PROCESSING),
                                 ("confirmed", CustomerStatus.PAID),
                                 ("failed", CustomerStatus.FAILED),
                                 ("unknown", CustomerStatus.UNKNOWN)):
            remote["status"] = status
            self.assertEqual(assess(self.ref, self.payment, self.client), expected)
        remote["amount"] = 1000
        with self.assertRaises(PaymentMismatch):
            assess(self.ref, self.payment, self.client)

    def test_other_environment_is_not_queried(self):
        self.ref.environment = "live"
        self.assertEqual(assess(self.ref, self.payment, self.client), CustomerStatus.UNKNOWN)
        self.client.billing_request.assert_not_called()

    def test_manual_review_never_shows_paid_or_offers_a_retry(self):
        self.ref.manual_review_required = True
        self.assertEqual(assess(self.ref, self.payment, self.client), CustomerStatus.FAILED)
        self.client.payment.assert_not_called()
        self.client.billing_request.assert_not_called()

    def test_inactive_mandate_without_payment_is_failed(self):
        self.ref.remote_payment_id = None
        self.ref.mandate_status = "cancelled"
        self.assertEqual(assess(self.ref, self.payment, self.client), CustomerStatus.FAILED)
        self.client.billing_request.assert_not_called()
