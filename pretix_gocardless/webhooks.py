"""Authenticated, fail-closed webhook delivery handling.

This is an experimental transport, not yet a complete payment integration.
The caller provides the event-specific handler and must raise on any failure.
"""
from .events import InvalidEvent, event_reference
from .security import InvalidWebhook, parse_webhook
from .configuration import credential


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


def process_reference(reference, event, provider, environment):
    """Fetch and validate a remote resource before changing local state."""
    if reference.resource_type not in {"payments", "billing_requests", "mandates"}:
        raise RuntimeError("Resource processing is not implemented")
    if reference.resource_type == "billing_requests" and (provider is None or environment != "sandbox"):
        raise RuntimeError("Only sandbox billing execution is available")
    from .client import Client
    from .models import PaymentReference
    from .reconcile import reconcile
    from .validation import validate_mandate
    client = Client(credential(getattr(provider, "account_settings", provider.settings), environment, "access_token"), environment)
    if reference.resource_type == "mandates":
        from .mandates import process_mandate
        return process_mandate(reference, event, environment, client)
    if reference.resource_type == "billing_requests":
        from .billing import process_billing
        payment_reference = PaymentReference.objects.get(
            billing_request_id=reference.resource_id, payment__order__event=event,
        )
        if payment_reference.environment != environment:
            raise RuntimeError("Payment environment mismatch")
        result = process_billing(payment_reference.pk, reference.event_id, environment, client)
        if provider.settings.get("ticket_release") == "pending":
            from .ticketing import release_pending
            release_pending(payment_reference.pk)
        return result
    payment_reference = PaymentReference.objects.get(
        remote_payment_id=reference.resource_id, payment__order__event=event,
    )
    if (environment != payment_reference.environment or not payment_reference.mandate_id
            or not payment_reference.creditor_id):
        raise RuntimeError("Payment context is incomplete")
    mandate = client.mandate(payment_reference.mandate_id)
    validate_mandate(mandate, payment_reference.mandate_id, payment_reference.creditor_id)
    result = reconcile(payment_reference.pk, reference.event_id, environment,
                       lambda: client.payment(reference.resource_id),
                       mandate_id=payment_reference.mandate_id, creditor_id=payment_reference.creditor_id)
    # The initial billing webhook can see pending_customer_approval. A later
    # payment event may be the first point at which release is permissible.
    if provider.settings.get("ticket_release") == "pending":
        from .ticketing import release_pending
        release_pending(payment_reference.pk)
    return result
