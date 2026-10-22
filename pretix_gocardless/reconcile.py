"""Experimental transactional payment reconciliation."""
import re

from django.db import transaction

from .status import Decision, decide
from .validation import validate_remote_payment, validate_recovered_payment


class ReconciliationError(Exception):
    pass


class RequiresManualReview(ReconciliationError):
    pass


def apply_status(reference, payment, status):
    """Apply one already validated remote payment status under row locks."""
    review_required = getattr(reference, "manual_review_required", False)
    action = decide(status, payment.state, reference.reversal_recorded)
    if review_required:
        # A late reversal stays visible until an operator resolves it. A later
        # snapshot may be stored as an event, but must not clear the evidence.
        return "manual_review"
    if action is Decision.REVIEW:
        raise RequiresManualReview("Unknown or inconsistent status requires review")
    if action is Decision.REVERSE:
        if reference.last_status not in {"failed", "cancelled", "customer_approval_denied", "charged_back"}:
            payment.order.log_action("pretix_gocardless.event.order.payment_reversed", {
                "local_id": payment.local_id, "gocardless_status": status,
            })
        reference.manual_review_required = True
    if action is Decision.CONFIRM:
        payment.confirm()
    elif action is Decision.FAIL:
        payment.fail(log_data={"gocardless_status": status})
        payment.order.log_action("pretix_gocardless.event.order.payment_failed", {
            "local_id": payment.local_id, "gocardless_status": status,
        })
        if reference.released_pending_tickets:
            from .ticketing import revoke_pending
            revoke_pending(reference, payment)
    reference.last_status = status
    reference.save(update_fields=["last_status", "manual_review_required"])
    return action.value


def reconcile(reference_id, event_id, environment, remote, *, mandate_id=None, creditor_id=None):
    """Reconcile an independently fetched payment while holding DB row locks.

    Caller must authenticate the webhook, ensure the remote object came from the
    correct creditor account and environment, and resolve reference_id safely.
    Late reversals are logged for manual review, not treated as refunds: pretix
    does not allow fail() on a confirmed payment. The financial state is left
    untouched until an operator resolves it.
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
        # A callable is evaluated under the reference/payment locks. Webhook
        # callers use this to avoid applying an API snapshot fetched before a
        # concurrent handler committed a newer state.
        if callable(remote):
            remote = remote()
        if mandate_id is not None:
            status = validate_recovered_payment(remote, reference.remote_payment_id, payment.amount, mandate_id)
        else:
            status = validate_remote_payment(remote, reference.remote_payment_id, payment.amount)
        result = apply_status(reference, payment, status)
        ProcessedEvent.objects.create(event_id=event_id, reference=reference)
        return result
