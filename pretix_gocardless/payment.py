"""Experimental sandbox-only pretix payment provider."""
from collections import OrderedDict

from django import forms
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from pretix.base.forms import SecretKeySettingsField
from pretix.base.payment import BasePaymentProvider, PaymentException
from .configuration import credential
from .creditors import creditor_field, resolve


class GoCardlessProvider(BasePaymentProvider):
    identifier = "gocardless"
    verbose_name = _("GoCardless (under development)")
    public_name = _("SEPA direct debit (GoCardless)")
    test_mode_message = _("Sandbox test only: no real payment is made.")

    @staticmethod
    def _logged_in_customer(request):
        return getattr(request, "customer", None) if request is not None else None

    def _environment(self, order=None):
        # Check the persisted order as well: the event can change mode later.
        if getattr(self.event, "testmode", False) or (order is not None and getattr(order, "testmode", False)):
            return "sandbox"
        return self.settings.get("environment")

    def _sandbox_ready(self, order=None):
        if not credential(self.settings, "sandbox", "access_token"):
            return False
        if credential(self.settings, "sandbox", "webhook_secret"):
            return True
        opted_in = self.settings.get("sandbox_without_webhook") in (True, "True", "true", "1", 1)
        return bool(opted_in and getattr(self.event, "testmode", False)
                    and (order is None or getattr(order, "testmode", False)))

    @property
    def settings_form_fields(self):
        fields = OrderedDict(super().settings_form_fields)
        sandbox_creditor = creditor_field(self.settings, "sandbox")
        live_creditor = creditor_field(self.settings, "live")
        fields.update([
            ("environment", forms.ChoiceField(
                label=_("GoCardless environment"),
                choices=(("sandbox", _("Sandbox")), ("live", _("Live"))),
                initial="sandbox",
                required=True,
                help_text=_("Test-mode events and test orders always use Sandbox, regardless of this selection. "
                            "Only Sandbox is currently supported. Do not use this development version for live payments."),
            )),
            ("sandbox_access_token", SecretKeySettingsField(
                label=_("Sandbox API access token"), required=False,
                help_text=format_html(
                    '{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    _("Create a sandbox token with read and write access; GoCardless Components are not required."),
                    "https://manage-sandbox.gocardless.com/developers/access-tokens",
                    _("Open sandbox access tokens"),
                ),
            )),
            ("sandbox_webhook_secret", SecretKeySettingsField(
                label=_("Sandbox webhook endpoint secret"), required=False,
                help_text=format_html(
                    '{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    _("Create a sandbox webhook endpoint and copy its secret. It needs a publicly reachable HTTPS URL."),
                    "https://docs.gocardless.com/docs/getting-started/stay-up-to-date-with-webhooks",
                    _("GoCardless webhook setup instructions"),
                ),
            )),
            ("sandbox_without_webhook", forms.BooleanField(
                label=_("Allow checkout tests without a webhook"), required=False, initial=False,
                help_text=_("Development only: works only for test-mode events and test orders. "
                            "Later payment updates are not received automatically."),
            )),
            ("sandbox_creditor_display" if sandbox_creditor.disabled else "sandbox_creditor_id", sandbox_creditor),
            ("live_access_token", SecretKeySettingsField(
                label=_("Live API access token (not yet enabled)"), required=False,
                help_text=format_html(
                    '{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    _("Create a separate live token with read and write access; live checkout is not yet enabled."),
                    "https://manage.gocardless.com/developers/access-tokens",
                    _("Open live access tokens"),
                ),
            )),
            ("live_webhook_secret", SecretKeySettingsField(
                label=_("Live webhook endpoint secret (not yet enabled)"), required=False,
                help_text=format_html(
                    '{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    _("Create a separate live webhook endpoint and copy its secret."),
                    "https://docs.gocardless.com/docs/getting-started/stay-up-to-date-with-webhooks",
                    _("GoCardless webhook setup instructions"),
                ),
            )),
            ("live_creditor_display" if live_creditor.disabled else "live_creditor_id", live_creditor),
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
        return (self.event.currency == "EUR" and self._environment() == "sandbox"
                and self._sandbox_ready()
                and super().is_allowed(request, total))

    def order_change_allowed(self, order, request=None):
        customer = self._logged_in_customer(request)
        return ((order.customer_id is None or (customer and order.customer_id == customer.pk))
                and self.event.currency == "EUR" and self._environment(order) == "sandbox"
                and self._sandbox_ready(order)
                and super().order_change_allowed(order, request))

    def payment_is_valid_session(self, request):
        return self._environment() == "sandbox" and self._sandbox_ready()

    def checkout_prepare(self, request, cart):
        return self.payment_is_valid_session(request)

    def payment_prepare(self, request, payment):
        customer = self._logged_in_customer(request)
        return ((payment.order.customer_id is None or (customer and payment.order.customer_id == customer.pk))
                and self._environment(payment.order) == "sandbox"
                and self._sandbox_ready(payment.order))

    def checkout_confirm_render(self, request, **kwargs):
        return _("After placing your order, you will be redirected to GoCardless to authorise a "
                 "reusable SEPA direct debit mandate. This plugin creates one payment for this order.")

    def execute_payment(self, request, payment):
        from pretix.multidomain.urlreverse import eventreverse_absolute
        from .checkout import start_authorisation
        from .client import Client, GoCardlessError

        customer = self._logged_in_customer(request)
        if payment.order.customer_id is not None and (not customer or payment.order.customer_id != customer.pk):
            raise PaymentException(_("Please sign in to the customer account associated with this order."))
        if (self._environment(payment.order) != "sandbox" or self.event.currency != "EUR"
                or not self._sandbox_ready(payment.order)):
            raise PaymentException(_("Only the GoCardless sandbox is supported in this development version."))
        return_url = eventreverse_absolute(self.event, "plugins:pretix_gocardless:return", kwargs={
            "order": payment.order.code, "payment": payment.pk,
            "hash": payment.order.tagged_secret("plugins:pretix_gocardless:return"),
        })
        try:
            url = start_authorisation(payment, Client(credential(self.settings, "sandbox", "access_token"), "sandbox"),
                                      "sandbox", resolve(self.settings, "sandbox"), return_url)
        except (GoCardlessError, ValueError, KeyError) as exc:
            raise PaymentException(_("Unable to start the GoCardless sandbox payment.")) from exc
        payment.state = payment.PAYMENT_STATE_PENDING
        payment.save(update_fields=["state"])
        return url

