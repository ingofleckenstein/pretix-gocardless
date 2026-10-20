"""Fail-closed pretix provider placeholder, not yet suitable for checkout."""
from collections import OrderedDict

from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.forms import SecretKeySettingsField
from pretix.base.payment import BasePaymentProvider, PaymentException


class GoCardlessProvider(BasePaymentProvider):
    identifier = "gocardless"
    verbose_name = _("GoCardless (under development)")
    public_name = _("SEPA direct debit")

    @property
    def settings_form_fields(self):
        fields = OrderedDict(super().settings_form_fields)
        fields.update([
            ("environment", forms.ChoiceField(
                label=_("GoCardless environment"),
                choices=(("sandbox", _("Sandbox")), ("live", _("Live"))),
                initial="sandbox",
                required=True,
                help_text=_("Not yet active: this provider cannot process payments."),
            )),
            ("access_token", SecretKeySettingsField(label=_("API access token"), required=False)),
            ("webhook_secret", SecretKeySettingsField(label=_("Webhook endpoint secret"), required=False)),
        ])
        return fields

    def is_allowed(self, request, total=None):
        return False

    def order_change_allowed(self, order, request=None):
        return False

    def payment_is_valid_session(self, request):
        return False

    def execute_payment(self, request, payment):
        raise PaymentException(_("GoCardless is not ready for payments."))
