"""Experimental transactional reconciliation; not exposed to webhooks yet."""
import re

from django.db import transaction

from .status import Decision, decide
from .validation import validate_remote_payment, validate_recovered_payment


class ReconciliationError(Exception):
    pass


class RequiresManualReview(ReconciliationError):
    pass


def reconcile(reference_id, event_id, environment, remote, *, mandate_id=None, creditor_id=None):
    """Reconcile an independently fetched payment while holding DB row locks.

    Caller must authenticate the webhook, ensure the remote object came from the
    correct creditor account and environment, and resolve reference_id safely.
    This routine deliberately does not implement chargeback/refund accounting.
    An exception rolls back both changes and the event marker for retry/review.
    """
    from .models import PaymentReference, ProcessedEvent
    from pretix.base.models import OrderPayment

    if not isinstance(event_id, str) or not re.fullmatch(r"EV[A-Za-z0-9]+", event_id):
        raise ReconciliationError("Invalid event identifier")
    with transaction.atomic():
        reference = PaymentReference.objects.select_for_update().get(pk=reference_id)
        if reference.environment != environment or not reference.remote_payment_id:
            raise ReconciliationError("Environment or payment reference mismatch")
        if mandate_id is not None and (reference.mandate_id != mandate_id or reference.creditor_id != creditor_id):
            raise ReconciliationError("Mandate or creditor reference changed")
        existing = ProcessedEvent.objects.filter(event_id=event_id).first()
        if existing:
            if existing.reference_id != reference.pk:
                raise ReconciliationError("Event belongs to another payment")
            return "duplicate"
        payment = OrderPayment.objects.select_for_update().get(pk=reference.payment_id)
        if payment.provider != "gocardless" or payment.order.event.currency != "EUR":
            raise ReconciliationError("Invalid pretix payment context")
        if mandate_id is not None:
            status = validate_recovered_payment(remote, reference.remote_payment_id, payment.amount, mandate_id)
        else:
            status = validate_remote_payment(remote, reference.remote_payment_id, payment.amount)
        action = decide(status, payment.state, reference.reversal_recorded)
        if action in (Decision.REVERSE, Decision.REVIEW):
            raise RequiresManualReview("Chargeback, reversal or unknown status requires review")
        if action is Decision.CONFIRM:
            payment.confirm()
        elif action is Decision.FAIL:
            payment.fail(log_data={"gocardless_status": status})
        reference.last_status = status
        reference.save(update_fields=["last_status"])
        ProcessedEvent.objects.create(event_id=event_id, reference=reference)
        return action.value
