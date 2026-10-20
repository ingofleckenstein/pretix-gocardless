from unittest import TestCase
from unittest.mock import patch
from django import forms
from pretix_gocardless.client import Client, GoCardlessError
from pretix_gocardless.creditors import creditor_field, resolve


class CreditorTests(TestCase):
    def test_single_creditor_is_readonly_and_resolved_without_input(self):
        with patch("pretix_gocardless.creditors.available", return_value=[("CR123", "Merchant")]):
            field = creditor_field({}, "sandbox")
            self.assertTrue(field.disabled)
            self.assertEqual(field.initial, "CR123")
            self.assertEqual(resolve({}, "sandbox"), "CR123")
            class TestForm(forms.Form):
                creditor = field
            form = TestForm(initial={"creditor": "CRSTALE"})
            self.assertEqual(form["creditor"].value(), "CR123")

    def test_multiple_require_an_explicit_valid_selection(self):
        with patch("pretix_gocardless.creditors.available", return_value=[("CR123", "A"), ("CR456", "B")]):
            self.assertIsInstance(creditor_field({}, "sandbox"), forms.ChoiceField)
            self.assertEqual(resolve({"sandbox_creditor_id": "CR456"}, "sandbox"), "CR456")
            for value in ("", "CR999"):
                with self.assertRaises(GoCardlessError):
                    resolve({"sandbox_creditor_id": value}, "sandbox")

    def test_api_failure_does_not_enable_editing(self):
        with patch("pretix_gocardless.creditors.available", side_effect=GoCardlessError("offline")):
            self.assertTrue(creditor_field({}, "sandbox").disabled)

    def test_creditor_pagination(self):
        client = Client("test-only", "sandbox")
        with patch.object(client, "request", side_effect=[
            {"creditors": [{"id": "CR123", "name": "A"}], "meta": {"cursors": {"after": "next"}}},
            {"creditors": [{"id": "CR456", "name": "B"}], "meta": {"cursors": {"after": None}}},
        ]):
            self.assertEqual(client.creditors(), [("CR123", "A"), ("CR456", "B")])

