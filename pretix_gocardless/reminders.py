"""Remind once per recipient about a verified incomplete mandate.

This uses pretix's normal transactional outgoing-mail queue. A transient API
or mail failure leaves the marker unset so a later periodic run can retry.
"""
import logging
from datetime import timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.db import transaction
from django.core.validators import validate_email
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)
CUSTOMER_DEFAULT = "Deine Bestellung ist eingegangen. Bitte öffne die Bestellseite und schließe die SEPA-Lastschrift ab."
ORGANIZER_DEFAULT = "Die SEPA-Lastschrift für eine Bestellung wurde noch nicht abgeschlossen. Bitte prüfe die Bestellung im Backend."
TEMPLATE = "pretix_gocardless/email/reminder.txt"


def send_for_reference(reference_id):
    from pretix.base.models import Order, OrderPayment
    from pretix.base.services.mail import mail
    from .client import Client
    from .configuration import credential
    from .customer_status import CustomerStatus, assess
    from .models import PaymentReference, ReminderDelivery
    from .payment import GoCardlessProvider

    # Locks serialize webhook updates and concurrent periodic invocations. The
    # API query is deliberately before queuing; no absent webhook is considered
    # proof of an abandoned mandate.
    with transaction.atomic():
        ref = PaymentReference.objects.select_for_update().get(pk=reference_id)
        payment = OrderPayment.objects.select_for_update().get(pk=ref.payment_id)
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        provider = GoCardlessProvider(order.event)
        enabled = provider.settings.get("reminder_enabled") in (True, "True", "true", "1", 1)
        if (not enabled or ref.environment != "sandbox" or provider._environment(order) != ref.environment
                or order.status != Order.STATUS_PENDING or order.require_approval
                or payment.provider != "gocardless" or payment.state not in {"created", "pending"}
                or order.expires <= timezone.now() or order.pending_sum < payment.amount
                or not ref.billing_request_id or ref.remote_payment_id
                or order.datetime > timezone.now() - timedelta(hours=1)):
            return False
        delivery, _ = ReminderDelivery.objects.get_or_create(payment=payment)
        organizer_email = provider.settings.get("reminder_organizer_email") or ""
        if delivery.customer_queued and (delivery.organizer_queued or not organizer_email):
            return False
        client = Client(credential(provider.account_settings, ref.environment, "access_token"), ref.environment)
        if assess(ref, payment, client) is not CustomerStatus.RESUME:
            return False
        context = {"event_name": str(order.event.name), "order_code": order.code}
        now = timezone.now()
        if not delivery.customer_queued and order.email:
            try:
                with transaction.atomic():
                    validate_email(order.email)
                    customer_context = dict(context, message=provider.settings.get("reminder_customer_text") or CUSTOMER_DEFAULT)
                    # pretix adds the private order URL only to the customer mail.
                    queued = mail(order.email, "Bitte schließe deine SEPA-Zahlung ab", TEMPLATE,
                                  customer_context, event=order.event, locale=order.locale, order=order,
                                  attach_tickets=False)
                    if queued:
                        order.log_action("pretix.event.order.email.sent", data=queued.log_data())
                        delivery.customer_queued = now
                        delivery.save(update_fields=["customer_queued"])
            except Exception:
                delivery.customer_queued = None
                logger.warning("GoCardless customer reminder could not be queued for reference %s", reference_id)
        if not delivery.organizer_queued and organizer_email:
            try:
                with transaction.atomic():
                    validate_email(organizer_email)
                    organizer_context = dict(context, message=provider.settings.get("reminder_organizer_text") or ORGANIZER_DEFAULT)
                    organizer_context["organizer_order_url"] = urljoin(settings.SITE_URL, reverse(
                        "control:event.order", kwargs={"organizer": order.event.organizer.slug,
                                                       "event": order.event.slug, "code": order.code}))
                    # No order or position is passed: pretix's HTML renderer only
                    # adds private customer links when it receives an order.
                    # no_order_links cannot be combined with HTML in pretix.
                    queued = mail(organizer_email, "SEPA-Einrichtung für Bestellung noch offen", TEMPLATE,
                                  organizer_context, event=order.event, locale=order.locale,
                                  attach_tickets=False)
                    if queued:
                        delivery.organizer_queued = now
                        delivery.save(update_fields=["organizer_queued"])
            except Exception:
                delivery.organizer_queued = None
                logger.warning("GoCardless organizer reminder could not be queued for reference %s", reference_id)
        return True


def run_reminders():
    from .models import PaymentReference
    cutoff = timezone.now() - timedelta(hours=1)
    # Iterate the entire eligible set: repeatedly skipping an unavailable or
    # already-reminded reference must never starve later orders.
    ids = (PaymentReference.objects.filter(
        environment="sandbox", payment__order__datetime__lte=cutoff, remote_payment_id__isnull=True,
        billing_request_id__isnull=False, payment__provider="gocardless",
        payment__state__in=("created", "pending"), payment__order__status="n",
        payment__order__expires__gt=timezone.now(),
    ).exclude(payment__gocardless_reminder__customer_queued__isnull=False,
              payment__gocardless_reminder__organizer_queued__isnull=False)
        .order_by("created").values_list("pk", flat=True).iterator(chunk_size=200))
    for reference_id in ids:
        try:
            send_for_reference(reference_id)
        except Exception:
            # Never log API payloads, addresses, tokens or customer links.
            logger.warning("GoCardless reminder could not be processed for reference %s", reference_id)
