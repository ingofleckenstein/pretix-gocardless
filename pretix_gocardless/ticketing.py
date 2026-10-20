"""Manage pretix's pending-order ticket validity without marking it paid."""
from django.db import transaction


def release_pending(reference_id):
    from pretix.base.models import Order
    from .models import PaymentReference

    with transaction.atomic():
        ref = PaymentReference.objects.select_for_update().get(pk=reference_id)
        payment = ref.payment
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if (ref.last_status not in {"pending_submission", "submitted"}
                or payment.state not in {"created", "pending"} or order.status != Order.STATUS_PENDING
                or order.require_approval or order.pending_sum > payment.amount):
            return False
        if order.valid_if_pending:
            return False  # Never claim an existing administrator/plugin setting.
        order.valid_if_pending = True
        order.save(update_fields=["valid_if_pending"])
        order.log_action("pretix.event.order.valid_if_pending.set", data={"provider": "gocardless"})
        ref.released_pending_tickets = True
        ref.save(update_fields=["released_pending_tickets"])
        return True


def revoke_pending(ref, payment):
    """Called within the reconciliation transaction for a failed debit."""
    if not ref.released_pending_tickets:
        return False
    from pretix.base.models import Order
    order = Order.objects.select_for_update().get(pk=payment.order_id)
    if order.status == Order.STATUS_PENDING and order.valid_if_pending:
        order.valid_if_pending = False
        order.save(update_fields=["valid_if_pending"])
        order.log_action("pretix.event.order.valid_if_pending.unset", data={"provider": "gocardless"})
    ref.released_pending_tickets = False
    ref.save(update_fields=["released_pending_tickets"])
    return True
