"""Experimental sandbox-only pretix payment provider."""
from collections import OrderedDict

from django import forms
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from pretix.base.forms import SecretKeySettingsField
from pretix.base.payment import BasePaymentProvider, PaymentException
from .configuration import OrganizerAccountSettings, credential
from .creditors import CreditorDisplayField, creditor_field, resolve


class CopyableReadonlyInput(forms.TextInput):
    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["attrs"].pop("disabled", None)
        context["widget"]["attrs"]["readonly"] = True
        return context


class GoCardlessProvider(BasePaymentProvider):
    identifier = "gocardless"
    verbose_name = _("GoCardless (under development)")
    public_name = _("SEPA direct debit (GoCardless)")
    test_mode_message = _("Sandbox test only: no real payment is made.")

    @property
    def account_settings(self):
        organizer = getattr(self.event, "organizer", None)
        return OrganizerAccountSettings(organizer.settings if organizer else {}, self.settings)

    @staticmethod
    def _logged_in_customer(request):
        return getattr(request, "customer", None) if request is not None else None

    def _environment(self, order=None):
        # Test orders stay in Sandbox even if the organizer later changes the
        # event setting. Existing payment references keep their own environment.
        if getattr(self.event, "testmode", False) or (order is not None and getattr(order, "testmode", False)):
            return "sandbox"
        return self.settings.get("environment")

    def _sandbox_ready(self, order=None):
        if not credential(self.account_settings, "sandbox", "access_token"):
            return False
        organizer = getattr(self.event, "organizer", None)
        if organizer is not None and organizer.settings.get("gocardless_sandbox_webhook_secret"):
            return True
        if credential(self.settings, "sandbox", "webhook_secret"):
            return True
        opted_in = self.settings.get("sandbox_without_webhook") in (True, "True", "true", "1", 1)
        return bool(opted_in and getattr(self.event, "testmode", False)
                    and (order is None or getattr(order, "testmode", False)))

    @property
    def settings_form_fields(self):
        from pretix.multidomain.urlreverse import eventreverse_absolute

        fields = OrderedDict(super().settings_form_fields)
        sandbox_creditor = creditor_field(self.account_settings, "sandbox")
        live_creditor = creditor_field(self.account_settings, "live")
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
            ("sandbox_webhook_url_display", CreditorDisplayField(
                label=_("Legacy event-specific Sandbox webhook URL"), required=False, disabled=True,
                widget=CopyableReadonlyInput,
                initial=eventreverse_absolute(self.event, "plugins:pretix_gocardless:webhook",
                                              kwargs={"environment": "sandbox"}),
                help_text=_("Legacy endpoint for existing configurations. For new setups use the organizer's GoCardless webhook settings."),
            )),
            ("sandbox_webhook_secret", SecretKeySettingsField(
                label=_("Legacy event-specific Sandbox webhook secret"), required=False,
                help_text=format_html(
                    '{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    _("GoCardless creates the endpoint secret. Copy it here; to replace it, rotate it in the GoCardless dashboard and update it here."),
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
            ("live_webhook_url_display", CreditorDisplayField(
                label=_("Live webhook URL (not yet enabled)"), required=False, disabled=True,
                widget=CopyableReadonlyInput,
                initial=eventreverse_absolute(self.event, "plugins:pretix_gocardless:webhook",
                                              kwargs={"environment": "live"}),
                help_text=_("Use this separate URL only for a live GoCardless endpoint. Live payments are not yet supported."),
            )),
            ("live_webhook_secret", SecretKeySettingsField(
                label=_("Live webhook endpoint secret (not yet enabled)"), required=False,
                help_text=format_html(
                    '{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                    _("GoCardless creates the separate live endpoint secret. Rotate it in GoCardless, then update it here."),
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
            ("chargeback_notice", forms.CharField(
                label=_("Checkout notice about returned direct debits"),
                required=False,
                widget=forms.Textarea(attrs={"rows": 3}),
                help_text=_("Optional text shown when customers select GoCardless and review their order. "
                            "This is a notice only; it does not charge any fee."),
            )),
            ("reminder_enabled", forms.BooleanField(
                label=_("Bei unvollständiger Lastschrift erinnern"),
                required=False, initial=False,
                help_text=_("Nach mindestens einer Stunde bei GoCardless prüfen und einmalig erinnern. Erfordert die regelmäßigen pretix-Hintergrundaufgaben."),
            )),
            ("reminder_organizer_email", forms.EmailField(
                label=_("E-Mail-Adresse für die Veranstalter-Erinnerung"), required=False,
                help_text=_("Leer lassen, um keine Veranstalter-Erinnerung zu senden."),
            )),
            ("reminder_customer_text", forms.CharField(
                label=_("Erinnerungstext für Kundinnen und Kunden"), required=False, widget=forms.Textarea(attrs={"rows": 4}),
                initial="Deine Bestellung ist eingegangen. Bitte öffne die Bestellseite und schließe die SEPA-Lastschrift ab.",
                help_text=_("pretix ergänzt den sicheren Bestelllink. Leer lassen für den Standardtext."),
            )),
            ("reminder_organizer_text", forms.CharField(
                label=_("Erinnerungstext für Veranstalter"), required=False, widget=forms.Textarea(attrs={"rows": 4}),
                initial="Die SEPA-Lastschrift für eine Bestellung wurde noch nicht abgeschlossen. Bitte prüfe die Bestellung im Backend.",
                help_text=_("Veranstaltung und Bestellnummer werden ergänzt; der Kundenlink wird nicht weitergegeben."),
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

    def _chargeback_notice(self):
        return self.settings.get("chargeback_notice") or ""

    def payment_form_render(self, request, total, order=None):
        notice = self._chargeback_notice()
        return format_html("<p>{}</p>", notice) if notice else ""

    def checkout_confirm_render(self, request, **kwargs):
        description = _("After placing your order, we will collect the amount once by SEPA direct debit. "
                 "If you are signed in and have an existing usable mandate, it may be reused; "
                 "otherwise you will be redirected to GoCardless to authorise a mandate. "
                 "This does not create a subscription.")
        notice = self._chargeback_notice()
        return format_html("<p>{}</p><p>{}</p>", description, notice) if notice else description

    def order_pending_mail_render(self, order, payment):
        return _("Your order has been received. Once payment is confirmed, your tickets will be sent according to the event's ticket settings. If you have not completed the direct debit authorisation, please open your order page to continue.")

    def payment_pending_render(self, request, payment):
        from django.middleware.csrf import get_token
        from pretix.multidomain.urlreverse import eventreverse
        from .client import Client
        from .customer_status import CustomerStatus, assess
        try:
            ref = payment.gocardless_reference
            if self._environment(payment.order) != ref.environment or not self._sandbox_ready(payment.order):
                raise ValueError("Payment configuration is unavailable")
            client = Client(credential(self.account_settings, ref.environment, "access_token"), ref.environment)
            state = assess(ref, payment, client)
        except Exception:
            state = CustomerStatus.UNKNOWN
        if state is CustomerStatus.RESUME:
            url = eventreverse(self.event, "plugins:pretix_gocardless:resume", kwargs={
                "order": payment.order.code, "payment": payment.pk,
                "hash": payment.order.tagged_secret("plugins:pretix_gocardless:resume"),
            })
            return format_html('<p>{}</p><form method="post" action="{}"><input type="hidden" name="csrfmiddlewaretoken" value="{}"><button class="btn btn-primary" type="submit">{}</button></form>',
                               _("Die Einrichtung der Lastschrift ist noch nicht abgeschlossen."), url, get_token(request),
                               _("Zahlung fortsetzen"))
        if state is CustomerStatus.PROCESSING:
            return format_html("<p>{}</p>", _("Die Lastschrift wird bearbeitet oder wartet auf Einzug. Bitte starte keine weitere Zahlung."))
        if state is CustomerStatus.PAID:
            return format_html("<p>{}</p>", _("GoCardless meldet die Zahlung als bestätigt. Deine Bestellung wird aktualisiert."))
        if state is CustomerStatus.FAILED:
            return format_html("<p>{}</p>", _("Die Lastschrift war nicht erfolgreich. Bitte kontaktiere den Veranstalter, bevor du es erneut versuchst."))
        return format_html("<p>{}</p>", _("Der Zahlungsstatus kann derzeit nicht geprüft werden. Versuche es später erneut oder kontaktiere den Veranstalter. Bitte zahle nicht ein zweites Mal."))

    def execute_payment(self, request, payment):
        from pretix.multidomain.urlreverse import eventreverse_absolute
        from .checkout import start_authorisation, start_reused_mandate
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
        if payment.state not in {"created", "pending"}:
            raise PaymentException(_("This payment is no longer awaiting collection."))
        if payment.state == "created":
            payment.state = payment.PAYMENT_STATE_PENDING
            payment.save(update_fields=["state"])
        try:
            client = Client(credential(self.account_settings, "sandbox", "access_token"), "sandbox")
            creditor = resolve(self.account_settings, "sandbox")
            reused = start_reused_mandate(payment, client, "sandbox", creditor)
            if reused:
                # An existing authorized mandate needs a new debit for this
                # order, but no second hosted consent flow.
                url = eventreverse_absolute(self.event, "presale:event.order", kwargs={
                    "order": payment.order.code, "secret": payment.order.secret,
                })
                if self.settings.get("ticket_release") == "pending":
                    from .ticketing import release_pending
                    release_pending(payment.gocardless_reference.pk)
            else:
                url = start_authorisation(payment, client, "sandbox", creditor, return_url)
        except (GoCardlessError, ValueError, KeyError) as exc:
            raise PaymentException(_("Unable to start the GoCardless sandbox payment.")) from exc
        return url
