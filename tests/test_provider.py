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

    def test_only_complete_sandbox_configuration_is_selectable(self):
        self.assertTrue(self.provider.is_allowed(object(), 1))
        for key, value in (("environment", "live"), ("sandbox_creditor_id", ""), ("sandbox_webhook_secret", "")):
            original = self.event.provider_settings[key]
            self.event.provider_settings[key] = value
            self.assertFalse(self.provider.is_allowed(object(), 1))
            self.event.provider_settings[key] = original
        self.event.currency = "GBP"
        self.assertFalse(self.provider.is_allowed(object(), 1))

    def test_execute_redirects_and_keeps_payment_pending(self):
        order = SimpleNamespace(code="ABC12", tagged_secret=Mock(return_value="secret"))
        payment = SimpleNamespace(pk=42, order=order, state="created", PAYMENT_STATE_PENDING="pending", save=Mock())
        with patch("pretix_gocardless.checkout.start_authorisation", return_value="https://pay-sandbox.gocardless.com/test"):
            url = self.provider.execute_payment(object(), payment)
        self.assertEqual(url, "https://pay-sandbox.gocardless.com/test")
        self.assertEqual(payment.state, "pending")
        payment.save.assert_called_once_with(update_fields=["state"])

    def test_execute_rejects_live(self):
        self.event.provider_settings["environment"] = "live"
        with self.assertRaises(self.provider_module.PaymentException):
            self.provider.execute_payment(object(), object())
