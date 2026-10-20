import unittest
from decimal import Decimal

from pretix_gocardless.validation import PaymentMismatch, validate_remote_payment, validate_recovered_payment, validate_mandate


class PaymentValidationTests(unittest.TestCase):
    def setUp(self):
        self.remote = {"id": "PM123", "amount": 1234, "currency": "EUR", "status": "confirmed"}

    def test_valid_payment(self):
        self.assertEqual(validate_remote_payment(self.remote, "PM123", Decimal("12.34")), "confirmed")

    def test_wrong_id_currency_and_amount_are_rejected(self):
        for field, value in (("id", "PM456"), ("currency", "GBP"), ("amount", 1235), ("amount", True), ("amount", "1234")):
            with self.subTest(field=field, value=value), self.assertRaises(PaymentMismatch):
                validate_remote_payment({**self.remote, field: value}, "PM123", Decimal("12.34"))

    def test_invalid_local_amount_and_status(self):
        for amount in ("NaN", "0", "-1", "12.345"):
            with self.subTest(amount=amount), self.assertRaises(PaymentMismatch):
                validate_remote_payment(self.remote, "PM123", amount)
        with self.assertRaises(PaymentMismatch):
            validate_remote_payment({**self.remote, "status": None}, "PM123", "12.34")

    def test_recovered_payment_must_match_original_mandate(self):
        remote = {**self.remote, "links": {"mandate": "MD123"}}
        self.assertEqual(validate_recovered_payment(remote, "PM123", "12.34", "MD123"), "confirmed")
        for links in (None, {}, {"mandate": "MD999"}):
            with self.subTest(links=links), self.assertRaises(PaymentMismatch):
                validate_recovered_payment({**remote, "links": links}, "PM123", "12.34", "MD123")

    def test_mandate_must_match_creditor_and_sepa_scheme(self):
        mandate = {"id": "MD123", "scheme": "sepa_core", "status": "active", "links": {"creditor": "CR123"}}
        self.assertEqual(validate_mandate(mandate, "MD123", "CR123"), "active")
        for change in ({"id": "MD999"}, {"scheme": "bacs"}, {"links": {"creditor": "CR999"}}, {"status": None}):
            with self.subTest(change=change), self.assertRaises(PaymentMismatch):
                validate_mandate({**mandate, **change}, "MD123", "CR123")


if __name__ == "__main__":
    unittest.main()
