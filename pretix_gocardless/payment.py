"""Experimental sandbox-only pretix payment provider."""
from collections import OrderedDict

from django import forms
from django.utils.translation import gettext_lazy as _
from pretix.base.forms import SecretKeySettingsField
from pretix.base.payment import BasePaymentProvider, PaymentException
from .configuration import credential


class GoCardlessProvider(BasePaymentProvider):
    identifier = "gocardless"
    verbose_name = _("GoCardless (under development)")
    public_name = _("SEPA direct debit")
    test_mode_message = _("Sandbox test only: no real payment is made.")

    @property
    def settings_form_fields(self):
        fields = OrderedDict(super().settings_form_fields)
        fields.update([
            ("environment", forms.ChoiceField(
                label=_("GoCardless environment"),
                choices=(("sandbox", _("Sandbox")), ("live", _("Live"))),
                initial="sandbox",
                required=True,
                help_text=_("Only Sandbox is currently supported. Do not use this development version for live payments."),
            )),
            ("sandbox_access_token", SecretKeySettingsField(label=_("Sandbox API access token"), required=False)),
            ("sandbox_webhook_secret", SecretKeySettingsField(label=_("Sandbox webhook endpoint secret"), required=False)),
            ("sandbox_creditor_id", forms.CharField(label=_("Sandbox creditor ID"), required=False)),
            ("live_access_token", SecretKeySettingsField(label=_("Live API access token (not yet enabled)"), required=False)),
            ("live_webhook_secret", SecretKeySettingsField(label=_("Live webhook endpoint secret (not yet enabled)"), required=False)),
            ("live_creditor_id", forms.CharField(label=_("Live creditor ID (not yet enabled)"), required=False)),
            ("ticket_release", forms.ChoiceField(
                label=_("Ticket release for direct debit"),
                choices=(("confirmed", _("After GoCardless confirms payment")),
                         ("pending", _("Earlier, while the direct debit is pending"))),
                initial="confirmed",
                required=True,
                help_text=_("Early release carries a risk of failed payments and chargebacks. "
                            "The current implementation is experimental and sandbox-only."),
            )),
        ])
        return fields

    def is_allowed(self, request, total=None):
        return (self.event.currency == "EUR" and self.settings.get("environment") == "sandbox"
                and all(credential(self.settings, "sandbox", k) for k in ("access_token", "creditor_id", "webhook_secret"))
                and super().is_allowed(request, total))

    def order_change_allowed(self, order, request=None):
        return (self.event.currency == "EUR" and self.settings.get("environment") == "sandbox"
                and all(credential(self.settings, "sandbox", k) for k in ("access_token", "creditor_id", "webhook_secret"))
                and super().order_change_allowed(order, request))

    def payment_is_valid_session(self, request):
        return self.settings.get("environment") == "sandbox"

    def checkout_prepare(self, request, cart):
        return self.payment_is_valid_session(request)

    def payment_prepare(self, request, payment):
        return self.payment_is_valid_session(request)

    def checkout_confirm_render(self, request, **kwargs):
        return _("After placing your order, you will be redirected to GoCardless to authorise the direct debit.")

    def execute_payment(self, request, payment):
        from pretix.multidomain.urlreverse import eventreverse_absolute
        from .checkout import start_authorisation
        from .client import Client, GoCardlessError

        if request is None or self.settings.get("environment") != "sandbox" or self.event.currency != "EUR":
            raise PaymentException(_("Only the GoCardless sandbox is supported in this development version."))
        return_url = eventreverse_absolute(self.event, "plugins:pretix_gocardless:return", kwargs={
            "order": payment.order.code, "payment": payment.pk,
            "hash": payment.order.tagged_secret("plugins:pretix_gocardless:return"),
        })
        try:
            url = start_authorisation(payment, Client(credential(self.settings, "sandbox", "access_token"), "sandbox"),
                                      "sandbox", credential(self.settings, "sandbox", "creditor_id"), return_url)
        except (GoCardlessError, ValueError, KeyError) as exc:
            raise PaymentException(_("Unable to start the GoCardless sandbox payment.")) from exc
        payment.state = payment.PAYMENT_STATE_PENDING
        payment.save(update_fields=["state"])
        return url
