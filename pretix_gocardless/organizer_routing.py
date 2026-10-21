"""Route account-wide GoCardless events to a known organizer payment.

This is not yet wired into a public endpoint; authentication must happen first.
"""


def route_reference(reference, organizer, environment, find_reference, process):
    """Dispatch a known payment within this organizer.

    The lookup must constrain organizer and environment. Missing references,
    lookup errors and processing errors propagate for redelivery.
    """
    if reference.resource_type not in {"billing_requests", "payments"}:
        raise RuntimeError("Resource processing is not implemented")
    payment_reference = find_reference(reference, organizer, environment)
    if payment_reference is None:
        # A payment webhook can overtake persistence of its local reference.
        # Never acknowledge it until that race can be distinguished from an
        # unrelated account event.
        raise RuntimeError("Payment reference not yet available")
    event = payment_reference.payment.order.event
    if event.organizer_id != organizer.pk or payment_reference.environment != environment:
        raise RuntimeError("Organizer or environment mismatch")
    return process(reference, event, environment)


def process_organizer_reference(reference, organizer, environment):
    """Resolve a reference inside one organizer before running the event handler.

    Caller must first verify the signature against that organizer's secret and
    establish the appropriate django-scopes organizer scope.
    """
    from .models import PaymentReference
    from .payment import GoCardlessProvider
    from .webhooks import process_reference

    def find(ref, owner, env):
        field = {"payments": "remote_payment_id", "billing_requests": "billing_request_id"}[ref.resource_type]
        return PaymentReference.objects.select_related("payment__order__event").filter(
            **{field: ref.resource_id, "environment": env,
               "payment__order__event__organizer": owner}
        ).first()

    def process(ref, event, env):
        return process_reference(ref, event, GoCardlessProvider(event), env)

    return route_reference(reference, organizer, environment, find, process)
