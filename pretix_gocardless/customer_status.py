"""Read-only status assessment for an interrupted hosted mandate flow.

Never infer a failed payment from a missing webhook. Unknown or inconsistent
remote data is intentionally not offered as a new payment attempt.
"""
from enum import Enum

from .validation import validate_recovered_payment


class CustomerStatus(Enum):
    RESUME = "resume"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"
    UNKNOWN = "unknown"


def assess(reference, payment, client):
    if getattr(reference, "manual_review_required", False):
        return CustomerStatus.FAILED
    if reference.environment != "sandbox" or not reference.creditor_id:
        return CustomerStatus.UNKNOWN
    if reference.remote_payment_id:
        remote = client.payment(reference.remote_payment_id)
        status = validate_recovered_payment(remote, reference.remote_payment_id,
                                            payment.amount, reference.mandate_id)
        if status in {"pending_customer_approval", "pending_submission", "submitted"}:
            return CustomerStatus.PROCESSING
        if status in {"confirmed", "paid_out"}:
            return CustomerStatus.PAID
        if status in {"failed", "cancelled", "customer_approval_denied", "charged_back"}:
            return CustomerStatus.FAILED
        return CustomerStatus.UNKNOWN
    if not reference.billing_request_id:
        return CustomerStatus.UNKNOWN
    billing = client.billing_request(reference.billing_request_id)
    links = billing.get("links") or {}
    mandate = billing.get("mandate_request") or {}
    if (billing.get("id") != reference.billing_request_id
            or links.get("creditor") != reference.creditor_id
            or mandate.get("scheme") != "sepa_core" or mandate.get("currency") != "EUR"
            or billing.get("payment_request") is not None):
        return CustomerStatus.UNKNOWN
    if billing.get("status") in {"pending", "ready_to_fulfil"}:
        return CustomerStatus.RESUME
    if billing.get("status") == "fulfilled":
        return CustomerStatus.PROCESSING
    return CustomerStatus.UNKNOWN
