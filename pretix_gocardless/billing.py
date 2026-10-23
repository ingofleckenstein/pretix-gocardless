"""Turn one fulfilled sandbox billing request into one linked debit.

The caller authenticates the webhook and binds its reference to the current
event before entering this service. Live billing execution remains disabled.
"""
import re
from django.db import transaction

from .debit import ensure_debit
from .reconcile import ReconciliationError, reconcile


def process_billing(reference_id, event_id, environment, client):
    from .models import CustomerLink, PaymentReference, ProcessedEvent
    from pretix.base.models import OrderPayment

    if not isinstance(event_id, str) or not re.fullmatch(r"EV[A-Za-z0-9]+", event_id):
        raise ReconciliationError("Invalid event identifier")
    # Serialize a billing event with other attempts for this payment. A
    # redelivered event returns before any remote debit creation.
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
                or (not ref.remote_payment_id and (
                    payment.state not in {"created", "pending"}
                    or getattr(payment.order, "status", "n") != "n"))):
            raise ReconciliationError("Payment cannot be started in this state")
        if not ref.remote_payment_id:
            from .checkout import locked_checkout_payment
            payment = locked_checkout_payment(payment)
        # A signed webhook proves where the event came from, but not that its
        # billing request belongs to this pretix customer. Bind that customer
        # before ensure_debit fetches the remote mandate and payment.
        expected_customer_id = None
        if payment.order.customer_id:
            customer_link = CustomerLink.objects.filter(
                customer_id=payment.order.customer_id, environment=environment,
                creditor_id=ref.creditor_id,
            ).first()
            if customer_link is None:
                raise ReconciliationError("Customer binding is missing")
            expected_customer_id = customer_link.remote_customer_id
        mandate_id, payment_id, _ = ensure_debit(
            client, billing_request_id=ref.billing_request_id, creditor_id=ref.creditor_id,
            amount=payment.amount, creation_key=ref.creation_key,
            known_mandate_id=ref.mandate_id, known_payment_id=ref.remote_payment_id,
            expected_customer_id=expected_customer_id,
        )
        ref.mandate_id = mandate_id
        ref.remote_payment_id = payment_id
        ref.save(update_fields=["mandate_id", "remote_payment_id"])
        # The second read is deliberate: apply a current, independently fetched
        # state with all validation and the event marker in the same transaction.
        return reconcile(ref.pk, event_id, environment, client.payment(payment_id),
                         mandate_id=mandate_id, creditor_id=ref.creditor_id)
