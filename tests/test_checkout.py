from unittest import TestCase
from unittest.mock import Mock
from uuid import uuid4

from pretix_gocardless.checkout import CheckoutMismatch, ensure_billing_request, ensure_hosted_flow
from pretix_gocardless.client import IdempotencyConflict


class CheckoutTests(TestCase):
    def setUp(self):
        self.client = Mock()
        self.key = uuid4()
        self.client.create_mandate_request.return_value = {"id": "BRQ123"}
        self.client.billing_request.return_value = {
            "id": "BRQ123", "status": "pending", "links": {"creditor": "CR123"},
            "mandate_request": {"scheme": "sepa_core", "currency": "EUR"}, "payment_request": None,
        }
        self.url = "https://example.org/return"
        self.flow = {"id": "BRF123", "links": {"billing_request": "BRQ123"},
                     "redirect_uri": self.url, "exit_uri": self.url, "auto_fulfil": True,
                     "lock_currency": True, "authorisation_url": "https://pay-sandbox.gocardless.com/flow"}
        self.client.create_flow.return_value = self.flow
        self.client.validate_flow_url.side_effect = lambda value: value

    def test_request_created_and_independently_validated(self):
        self.assertEqual(ensure_billing_request(self.client, "CR123", self.key), "BRQ123")
        self.client.billing_request.assert_called_once_with("BRQ123")

    def test_existing_request_and_conflict_recovery(self):
        self.client.create_mandate_request.side_effect = IdempotencyConflict("BRQ123")
        self.assertEqual(ensure_billing_request(self.client, "CR123", self.key), "BRQ123")
        self.client.create_mandate_request.reset_mock()
        self.assertEqual(ensure_billing_request(self.client, "CR123", self.key, "BRQ123"), "BRQ123")
        self.client.create_mandate_request.assert_not_called()

    def test_wrong_creditor_or_existing_payment_request_is_rejected(self):
        self.client.billing_request.return_value["links"]["creditor"] = "CR999"
        with self.assertRaises(CheckoutMismatch):
            ensure_billing_request(self.client, "CR123", self.key)
        self.client.billing_request.return_value["links"]["creditor"] = "CR123"
        self.client.billing_request.return_value["payment_request"] = {"amount": 5}
        with self.assertRaises(CheckoutMismatch):
            ensure_billing_request(self.client, "CR123", self.key)

    def test_flow_conflict_fetches_and_validates_original_flow(self):
        self.client.create_flow.side_effect = IdempotencyConflict("BRF123")
        self.client.flow.return_value = self.flow
        self.assertEqual(ensure_hosted_flow(self.client, "BRQ123", self.key, self.url), self.flow["authorisation_url"])
        self.client.flow.assert_called_once_with("BRF123")

    def test_flow_from_other_billing_request_is_rejected(self):
        self.flow["links"]["billing_request"] = "BRQ999"
        with self.assertRaises(CheckoutMismatch):
            ensure_hosted_flow(self.client, "BRQ123", self.key, self.url)
