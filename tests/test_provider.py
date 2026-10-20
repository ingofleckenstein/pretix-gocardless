"""Provider interface tests with small pretix stubs, not a pretix integration test."""
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django import forms


class ProviderTests(TestCase):
    def setUp(self):
        forms_module = ModuleType("pretix.base.forms")
        forms_module.SecretKeySettingsField = forms.CharField
        payment_module = ModuleType("pretix.base.payment")

        class BaseProvider:
            settings_form_fields = {}

            def __init__(self, event):
                self.event = event
                self.settings = event.provider_settings

            def is_allowed(self, request, total):
                return True

            def order_change_allowed(self, order, request):
                return True

        payment_module.BasePaymentProvider = BaseProvider
        payment_module.PaymentException = type("PaymentException", (Exception,), {})
        reverse_module = ModuleType("pretix.multidomain.urlreverse")
        reverse_module.eventreverse_absolute = Mock(return_value="https://example.org/return")
        self.modules = patch.dict(sys.modules, {"pretix.base.forms": forms_module,
                                              "pretix.base.payment": payment_module,
                                              "pretix.multidomain.urlreverse": reverse_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("pretix_gocardless.payment", None)
        self.addCleanup(sys.modules.pop, "pretix_gocardless.payment", None)
        self.provider_module = importlib.import_module("pretix_gocardless.payment")
        self.event = SimpleNamespace(currency="EUR", provider_settings={"environment": "sandbox", "sandbox_access_token": "token", "sandbox_creditor_id": "CR123", "sandbox_webhook_secret": "secret"})
        self.provider = self.provider_module.GoCardlessProvider(self.event)
        self.request = SimpleNamespace(customer=SimpleNamespace(pk=123))
        available = patch("pretix_gocardless.creditors.available", return_value=[("CR123", "Test")])
        available.start()
        self.addCleanup(available.stop)

    def test_only_complete_sandbox_configuration_is_selectable(self):
        self.assertTrue(self.provider.is_allowed(self.request, 1))
        for key, value in (("environment", "live"), ("sandbox_access_token", ""), ("sandbox_webhook_secret", "")):
            original = self.event.provider_settings[key]
            self.event.provider_settings[key] = value
            self.assertFalse(self.provider.is_allowed(self.request, 1))
            self.event.provider_settings[key] = original
        self.event.currency = "GBP"
        self.assertFalse(self.provider.is_allowed(self.request, 1))

    def test_execute_redirects_and_keeps_payment_pending(self):
        order = SimpleNamespace(code="ABC12", customer_id=123, tagged_secret=Mock(return_value="secret"))
        payment = SimpleNamespace(pk=42, order=order, state="created", PAYMENT_STATE_PENDING="pending", save=Mock())
        with patch("pretix_gocardless.checkout.start_authorisation", return_value="https://pay-sandbox.gocardless.com/test"):
            url = self.provider.execute_payment(self.request, payment)
        self.assertEqual(url, "https://pay-sandbox.gocardless.com/test")
        self.assertEqual(payment.state, "pending")
        payment.save.assert_called_once_with(update_fields=["state"])

    def test_execute_rejects_live(self):
        self.event.provider_settings["environment"] = "live"
        with self.assertRaises(self.provider_module.PaymentException):
            self.provider.execute_payment(self.request, SimpleNamespace(order=SimpleNamespace(customer_id=123)))

    def test_event_testmode_overrides_live_and_uses_sandbox_credentials(self):
        self.event.testmode = True
        self.event.provider_settings.update(environment="live", live_access_token="live-token")
        order = SimpleNamespace(code="ABC12", customer_id=123, testmode=True, tagged_secret=Mock(return_value="secret"))
        payment = SimpleNamespace(pk=42, order=order, state="created", PAYMENT_STATE_PENDING="pending", save=Mock())
        self.assertTrue(self.provider.is_allowed(self.request, 1))
        with patch("pretix_gocardless.checkout.start_authorisation", return_value="https://pay-sandbox.gocardless.com/test") as start:
            self.provider.execute_payment(self.request, payment)
        self.assertEqual(start.call_args.args[1].environment, "sandbox")
        self.assertEqual(start.call_args.args[1].token, "token")
        self.assertEqual(start.call_args.args[2], "sandbox")
        self.event.provider_settings["sandbox_access_token"] = ""
        self.assertFalse(self.provider.is_allowed(self.request, 1))

    def test_old_test_order_stays_in_sandbox_after_event_changes_mode(self):
        self.event.testmode = False
        self.event.provider_settings["environment"] = "live"
        order = SimpleNamespace(customer_id=123, testmode=True)
        self.assertTrue(self.provider.order_change_allowed(order, self.request))
        self.assertTrue(self.provider.payment_prepare(self.request, SimpleNamespace(order=order)))
        self.assertEqual(self.provider._environment(order), "sandbox")
        order.testmode = False
        self.assertEqual(self.provider._environment(order), "live")

    def test_opt_in_without_webhook_only_allows_test_event_and_test_order(self):
        self.event.provider_settings.update(sandbox_webhook_secret="", sandbox_without_webhook=True)
        self.event.testmode = False
        self.assertFalse(self.provider.is_allowed(self.request, 1))
        self.event.testmode = True
        self.assertTrue(self.provider.is_allowed(self.request, 1))
        self.assertTrue(self.provider.payment_is_valid_session(self.request))
        order = SimpleNamespace(customer_id=123, testmode=False)
        self.assertFalse(self.provider.order_change_allowed(order, self.request))
        self.assertFalse(self.provider.payment_prepare(self.request, SimpleNamespace(order=order)))
        order.testmode = True
        self.assertTrue(self.provider.order_change_allowed(order, self.request))
        self.assertTrue(self.provider.payment_prepare(self.request, SimpleNamespace(order=order)))
        self.event.provider_settings["sandbox_without_webhook"] = "False"
        self.assertFalse(self.provider.is_allowed(self.request, 1))
        self.event.provider_settings["sandbox_without_webhook"] = True
        self.event.provider_settings["sandbox_access_token"] = ""
        self.assertFalse(self.provider.is_allowed(self.request, 1))

    def test_no_webhook_bypass_never_executes_a_non_test_order(self):
        self.event.testmode = True
        self.event.provider_settings.update(sandbox_webhook_secret="", sandbox_without_webhook=True)
        order = SimpleNamespace(customer_id=123, testmode=False)
        with self.assertRaises(self.provider_module.PaymentException):
            self.provider.execute_payment(self.request, SimpleNamespace(order=order))

    def test_guests_can_pay_and_other_customers_cannot_start_account_order(self):
        guest = SimpleNamespace(customer=None)
        self.assertTrue(self.provider.is_allowed(guest, 1))
        self.assertTrue(self.provider.payment_is_valid_session(guest))
        self.assertTrue(self.provider.order_change_allowed(SimpleNamespace(customer_id=None), guest))
        self.assertFalse(self.provider.order_change_allowed(SimpleNamespace(customer_id=123), guest))
        self.assertFalse(self.provider.order_change_allowed(SimpleNamespace(customer_id=456), self.request))
        self.assertTrue(self.provider.order_change_allowed(SimpleNamespace(customer_id=123), self.request))
        for request, owner in ((guest, 123), (self.request, 456)):
            with self.assertRaises(self.provider_module.PaymentException):
                self.provider.execute_payment(request, SimpleNamespace(order=SimpleNamespace(customer_id=owner)))
        order = SimpleNamespace(code="ABC12", customer_id=None, tagged_secret=Mock(return_value="secret"))
        payment = SimpleNamespace(pk=42, order=order, state="created", PAYMENT_STATE_PENDING="pending", save=Mock())
        with patch("pretix_gocardless.checkout.start_authorisation", return_value="https://pay-sandbox.gocardless.com/test"):
            self.assertEqual(self.provider.execute_payment(guest, payment), "https://pay-sandbox.gocardless.com/test")

    def test_configuration_links_are_environment_specific(self):
        fields = self.provider.settings_form_fields
        sandbox = str(fields["sandbox_access_token"].help_text)
        live = str(fields["live_access_token"].help_text)
        self.assertIn('href="https://manage-sandbox.gocardless.com/developers/access-tokens"', sandbox)
        self.assertIn('href="https://manage.gocardless.com/developers/access-tokens"', live)
        self.assertIn("noopener noreferrer", sandbox)
        for env in ("sandbox", "live"):
            self.assertIn("https://docs.gocardless.com/docs/getting-started/stay-up-to-date-with-webhooks",
                          str(fields[f"{env}_webhook_secret"].help_text))

    def test_single_uses_separate_readonly_display_and_multiple_use_selection(self):
        fields = self.provider.settings_form_fields
        self.assertEqual(fields["sandbox_creditor_display"].initial, "CR123")
        self.assertTrue(fields["sandbox_creditor_display"].disabled)
        self.assertNotIn("sandbox_creditor_id", fields)
        with patch("pretix_gocardless.creditors.available", return_value=[("CR123", "A"), ("CR456", "B")]):
            fields = self.provider.settings_form_fields
            self.assertIsInstance(fields["sandbox_creditor_id"], forms.ChoiceField)
            self.assertNotIn("sandbox_creditor_display", fields)

