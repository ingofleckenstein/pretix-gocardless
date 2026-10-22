from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import Mock
from types import SimpleNamespace
from uuid import uuid4

from pretix_gocardless.checkout import CheckoutMismatch, ensure_billing_request, ensure_hosted_flow, reusable_bank_account, verified_reusable_mandate
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
                     "lock_currency": True, "authorisation_url": "https://pay-sandbox.gocardless.com/flow",
                     "expires_at": "2099-01-01T00:00:00Z"}
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
        hosted = ensure_hosted_flow(self.client, "BRQ123", self.key, self.url)
        self.assertEqual((hosted.id, hosted.url), ("BRF123", self.flow["authorisation_url"]))
        self.client.flow.assert_called_once_with("BRF123")

    def test_known_unexpired_flow_is_fetched_without_creating_another(self):
        self.client.flow.return_value = self.flow
        hosted = ensure_hosted_flow(self.client, "BRQ123", self.key, self.url, known_id="BRF123")
        self.assertFalse(hosted.expired(datetime(2098, 1, 1, tzinfo=timezone.utc)))
        self.client.flow.assert_called_once_with("BRF123")
        self.client.create_flow.assert_not_called()

    def test_expired_flow_is_detected(self):
        self.flow["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.client.flow.return_value = self.flow
        hosted = ensure_hosted_flow(self.client, "BRQ123", self.key, self.url, known_id="BRF123")
        self.assertTrue(hosted.expired())

    def test_flow_from_other_billing_request_is_rejected(self):
        self.flow["links"]["billing_request"] = "BRQ999"
        with self.assertRaises(CheckoutMismatch):
            ensure_hosted_flow(self.client, "BRQ123", self.key, self.url)

    def test_flow_requires_matching_id_and_expiry(self):
        self.flow["id"] = "BRF999"
        self.client.flow.return_value = self.flow
        with self.assertRaises(CheckoutMismatch):
            ensure_hosted_flow(self.client, "BRQ123", self.key, self.url, known_id="BRF123")
        self.flow["id"] = "BRF123"
        self.flow.pop("expires_at")
        with self.assertRaises(CheckoutMismatch):
            ensure_hosted_flow(self.client, "BRQ123", self.key, self.url)

    def test_existing_bank_account_is_attached_and_checked(self):
        self.client.billing_request.return_value["links"].update(customer="CU123", customer_bank_account="BA123")
        self.assertEqual(ensure_billing_request(self.client, "CR123", self.key, customer_id="CU123", bank_account_id="BA123"), "BRQ123")
        self.assertEqual(self.client.create_mandate_request.call_args.kwargs, {"customer_id": "CU123", "bank_account_id": "BA123"})
        self.client.billing_request.return_value["links"]["customer_bank_account"] = "BA999"
        with self.assertRaises(CheckoutMismatch):
            ensure_billing_request(self.client, "CR123", self.key, customer_id="CU123", bank_account_id="BA123")

    def test_previous_account_must_be_enabled_and_belong_to_customer(self):
        self.client.billing_request.return_value.update(status="fulfilled", links={"creditor": "CR123", "customer": "CU123", "customer_bank_account": "BA123"})
        self.client.customer_bank_account.return_value = {"id": "BA123", "enabled": True, "currency": "EUR", "links": {"customer": "CU123"}}
        self.assertEqual(reusable_bank_account(self.client, "BRQ123", "CR123", "CU123"), "BA123")
        self.client.customer_bank_account.return_value["enabled"] = False
        self.assertIsNone(reusable_bank_account(self.client, "BRQ123", "CR123", "CU123"))
        self.client.customer_bank_account.return_value["links"]["customer"] = "CU999"
        with self.assertRaises(CheckoutMismatch):
            reusable_bank_account(self.client, "BRQ123", "CR123", "CU123")

    def test_reusable_mandate_checks_customer_bank_creditor_and_status(self):
        previous = SimpleNamespace(billing_request_id="BRQ123", mandate_id="MD123")
        self.client.billing_request.return_value.update(
            status="fulfilled", links={"creditor": "CR123", "customer": "CU123", "customer_bank_account": "BA123"},
            mandate_request={"scheme": "sepa_core", "currency": "EUR", "links": {"mandate": "MD123"}})
        self.client.customer_bank_account.return_value = {"id": "BA123", "enabled": True, "currency": "EUR", "links": {"customer": "CU123"}}
        self.client.mandate.return_value = {"id": "MD123", "scheme": "sepa_core", "status": "active", "links": {"creditor": "CR123", "customer_bank_account": "BA123"}}
        self.assertEqual(verified_reusable_mandate(self.client, previous, "CR123", "CU123"), "MD123")
        self.client.mandate.return_value["status"] = "cancelled"
        self.assertIsNone(verified_reusable_mandate(self.client, previous, "CR123", "CU123"))
        self.client.billing_request.return_value["links"]["customer"] = "CU999"
        with self.assertRaises(CheckoutMismatch):
            verified_reusable_mandate(self.client, previous, "CR123", "CU123")
