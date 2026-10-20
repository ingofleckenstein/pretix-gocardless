"""Experimental transactional bridge for fulfilled billing requests.

Checkout remains disabled. The caller must authenticate the webhook and bind
the reference to the current event before calling this service.
"""
import re
from django.db import transaction

from .debit import ensure_debit
from .reconcile import ReconciliationError, reconcile


def process_billing(reference_id, event_id, environment, client):
    from .models import PaymentReference, ProcessedEvent
    from pretix.base.models import OrderPayment

    if not isinstance(event_id, str) or not re.fullmatch(r"EV[A-Za-z0-9]+", event_id):
        raise ReconciliationError("Invalid event identifier")
    with transaction.atomic():
        ref = PaymentReference.objects.select_for_update().get(pk=reference_id)
        if ref.environment != environment or not ref.creditor_id or not ref.billing_request_id:
            raise ReconciliationError("Billing context is incomplete")
        existing = ProcessedEvent.objects.filter(event_id=event_id).first()
        if existing:
            if existing.reference_id != ref.pk:
                raise ReconciliationError("Event belongs to another payment")
            return "duplicate"
        payment = OrderPayment.objects.select_for_update().get(pk=ref.payment_id)
        if (payment.provider != "gocardless" or payment.order.event.currency != "EUR"
                or (not ref.remote_payment_id and payment.state not in {"created", "pending"})):
            raise ReconciliationError("Payment cannot be started in this state")
        mandate_id, payment_id, _ = ensure_debit(
            client, billing_request_id=ref.billing_request_id, creditor_id=ref.creditor_id,
            amount=payment.amount, creation_key=ref.creation_key,
            known_mandate_id=ref.mandate_id, known_payment_id=ref.remote_payment_id,
        )
        ref.mandate_id = mandate_id
        ref.remote_payment_id = payment_id
        ref.save(update_fields=["mandate_id", "remote_payment_id"])
        # The second read is deliberate: apply a current, independently fetched
        # state with all validation and the event marker in the same transaction.
        return reconcile(ref.pk, event_id, environment, client.payment(payment_id),
                         mandate_id=mandate_id, creditor_id=ref.creditor_id)
