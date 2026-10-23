"""Create or recover the one debit belonging to a completed billing request.

The sandbox billing webhook calls this service after the customer finishes the
hosted flow. Its caller locks the local reference and persists the creation key
before the first API request. Retries use that same key, including after a
timeout; they must never start a second debit for the same pretix payment.
"""
from .client import IdempotencyConflict
from .idempotency import operation_key
from .validation import fulfilled_mandate_id, validate_mandate, validate_recovered_payment


class MandateNotReady(Exception):
    pass


def ensure_debit(client, *, billing_request_id, creditor_id, amount, creation_key,
                 known_mandate_id=None, known_payment_id=None, expected_customer_id=None):
    """Create or recover one debit after verifying the completed mandate.

    Never trusts the redirect or webhook body to establish a payment. All
    objects are independently fetched and checked against the original intent.
    """
    billing = client.billing_request(billing_request_id)
    mandate_id = fulfilled_mandate_id(billing, billing_request_id, creditor_id)
    if expected_customer_id is not None:
        links = billing.get("links")
        if not isinstance(links, dict) or links.get("customer") != expected_customer_id:
            raise ValueError("Billing request customer mismatch")
    if known_mandate_id is not None and known_mandate_id != mandate_id:
        raise ValueError("Mandate changed for existing payment")
    mandate = client.mandate(mandate_id)
    mandate_status = validate_mandate(mandate, mandate_id, creditor_id)
    if not known_payment_id and mandate_status not in {"pending_submission", "submitted", "active"}:
        raise MandateNotReady("Mandate is not available for collection")
    # Once a remote payment ID is stored, recover and validate that payment
    # even if the mandate has since been cancelled. The debit has its own life.
    if known_payment_id:
        payment_id = known_payment_id
    else:
        try:
            payment = client.create_debit_payment(
                mandate_id, amount, operation_key(creation_key, "debit-payment")
            )
            payment_id = payment.get("id")
        except IdempotencyConflict as exc:
            payment_id = exc.resource_id
    remote = client.payment(payment_id)
    status = validate_recovered_payment(remote, payment_id, amount, mandate_id)
    return mandate_id, payment_id, status
