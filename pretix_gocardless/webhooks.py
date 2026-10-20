"""Authenticated, fail-closed webhook delivery handling.

This is an experimental transport, not yet a complete payment integration.
The caller provides the event-specific handler and must raise on any failure.
"""
from .events import InvalidEvent, event_reference
from .security import InvalidWebhook, parse_webhook


def handle_delivery(body, signature, secret, handler):
    """Authenticate first and acknowledge only when every relevant event succeeds.

    Returns an HTTP status code. A handler must be idempotent: if a later event
    fails, GoCardless may redeliver events that were already processed.
    """
    try:
        events = parse_webhook(body, signature, secret)
    except InvalidWebhook:
        return 498
    try:
        references = [event_reference(event) for event in events]
    except InvalidEvent:
        return 400
    try:
        for reference in references:
            if reference is not None:
                handler(reference)
    except Exception:
        # Do not log payloads, secrets, or potentially sensitive exception text.
        return 503
    return 204


def process_reference(reference, event, provider):
    """Fetch and validate a payment within an event before reconciling it.

    Billing-request and mandate events still require implementation and are
    deliberately retried rather than acknowledged. The provider is disabled.
    """
    if reference.resource_type != "payments":
        raise RuntimeError("Resource processing is not implemented")
    from .client import Client
    from .models import PaymentReference
    from .reconcile import reconcile
    from .validation import validate_mandate
    payment_reference = PaymentReference.objects.get(
        remote_payment_id=reference.resource_id, payment__order__event=event,
    )
    environment = provider.settings.get("environment")
    if (environment != payment_reference.environment or not payment_reference.mandate_id
            or not payment_reference.creditor_id):
        raise RuntimeError("Payment context is incomplete")
    client = Client(provider.settings.get("access_token"), environment)
    remote = client.payment(reference.resource_id)
    mandate = client.mandate(payment_reference.mandate_id)
    validate_mandate(mandate, payment_reference.mandate_id, payment_reference.creditor_id)
    return reconcile(payment_reference.pk, reference.event_id, environment, remote,
                     mandate_id=payment_reference.mandate_id, creditor_id=payment_reference.creditor_id)
