"""Transactional handling for authenticated mandate lifecycle events."""
import re

from django.db import transaction

from .reconcile import ReconciliationError, apply_status
from .validation import validate_mandate, validate_recovered_payment


INACTIVE_MANDATE_STATUSES = {"failed", "cancelled", "expired", "consumed", "blocked"}
KNOWN_MANDATE_STATUSES = INACTIVE_MANDATE_STATUSES | {"pending_submission", "submitted", "active"}


def process_mandate(reference, event, environment, client):
    """Persist a mandate state and reconcile every locally linked payment.

    A mandate can be reused for several orders. The event is therefore handled
    once for the organizer while every payment keeps its own independently
    fetched payment status. Mandate cancellation alone never reverses a
    confirmed payment.
    """
    from .models import PaymentReference, ProcessedEvent
    from pretix.base.models import OrderPayment

    if (not isinstance(reference.event_id, str)
            or not re.fullmatch(r"EV[A-Za-z0-9]+", reference.event_id)
            or not isinstance(reference.resource_id, str)
            or not re.fullmatch(r"MD[A-Za-z0-9]+", reference.resource_id)):
        raise ReconciliationError("Invalid mandate event")
    if getattr(reference, "action", None) not in {
        None, "failed", "cancelled", "expired", "consumed", "blocked",
    }:
        # Replaced/transferred mandates carry additional links and need their
        # own atomic rebinding workflow. Retrying is safer than acknowledging.
        raise ReconciliationError("Mandate action requires explicit handling")

    with transaction.atomic():
        # A signed mandate event can concern several pretix orders because a
        # signed-in customer may reuse one mandate. Lock all local references
        # in a stable order so each linked debit is considered exactly once.
        references = list(
            PaymentReference.objects.select_for_update()
            .select_related("payment__order__event")
            .filter(
                mandate_id=reference.resource_id,
                environment=environment,
                payment__order__event__organizer=event.organizer,
            )
            .order_by("pk")
        )
        if not references:
            raise ReconciliationError("Mandate reference not yet available")
        existing = ProcessedEvent.objects.filter(event_id=reference.event_id).first()
        reference_ids = {item.pk for item in references}
        if existing:
            if existing.reference_id not in reference_ids:
                raise ReconciliationError("Event belongs to another mandate")
            return "duplicate"

        creditors = {item.creditor_id for item in references}
        if len(creditors) != 1 or None in creditors or "" in creditors:
            raise ReconciliationError("Mandate creditor binding is inconsistent")
        creditor_id = next(iter(creditors))
        mandate = client.mandate(reference.resource_id)
        mandate_status = validate_mandate(mandate, reference.resource_id, creditor_id)
        if mandate_status not in KNOWN_MANDATE_STATUSES:
            raise ReconciliationError("Unknown mandate status")
        if (getattr(reference, "action", None) in INACTIVE_MANDATE_STATUSES
                and mandate_status not in INACTIVE_MANDATE_STATUSES):
            raise ReconciliationError("Mandate event and current status disagree")

        results = []
        for item in references:
            payment = OrderPayment.objects.select_for_update().get(pk=item.payment_id)
            if (payment.provider != "gocardless" or payment.order.event.currency != "EUR"
                    or payment.order.event.organizer_id != event.organizer_id):
                raise ReconciliationError("Invalid pretix payment context")
            item.mandate_status = mandate_status
            item.save(update_fields=["mandate_status"])
            if item.remote_payment_id:
                # Cancelling the mandate prevents future collections but does
                # not tell us whether this already created debit will settle.
                # Its own current GoCardless status decides the pretix payment.
                remote = client.payment(item.remote_payment_id)
                payment_status = validate_recovered_payment(
                    remote, item.remote_payment_id, payment.amount, item.mandate_id,
                )
                results.append(apply_status(item, payment, payment_status))
            elif mandate_status in INACTIVE_MANDATE_STATUSES and payment.state in {"created", "pending"}:
                # Without any remote debit there is nothing that can still
                # settle; close the pending local attempt and its early ticket.
                payment.fail(log_data={"gocardless_mandate_status": mandate_status})
                payment.order.log_action("pretix_gocardless.event.order.mandate_inactive", {
                    "local_id": payment.local_id, "gocardless_mandate_status": mandate_status,
                })
                if item.released_pending_tickets:
                    from .ticketing import revoke_pending
                    revoke_pending(item, payment)
                results.append("fail")
            else:
                results.append("noop")

        ProcessedEvent.objects.create(event_id=reference.event_id, reference=references[0])
        return "mandate_" + mandate_status + ":" + ",".join(results)
