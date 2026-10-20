"""Pure reconciliation decisions; not a substitute for transactional persistence.

These decisions must only be applied to an authenticated, current API response
after verifying its payment identifier, amount, currency and account context.
"""
from enum import Enum


class Decision(Enum):
    WAIT = "wait"
    CONFIRM = "confirm"
    FAIL = "fail"
    REVERSE = "reverse"
    NOOP = "noop"
    REVIEW = "review"


def decide(remote_status, local_status, reversal_recorded=False):
    """Return the intended action without changing any payment or order.

    A late failure/chargeback cannot use pretix's `fail()` once confirmed. The
    caller must record a reversal exactly once under a database lock, and handle
    accounting and order consequences separately. Unknown states fail closed.
    """
    if remote_status in {"pending_customer_approval", "pending_submission", "submitted"}:
        return Decision.WAIT if local_status in {"created", "pending"} else Decision.NOOP
    if remote_status in {"confirmed", "paid_out"}:
        if reversal_recorded:
            return Decision.REVIEW
        if local_status in {"created", "pending", "failed", "canceled"}:
            return Decision.CONFIRM
        return Decision.NOOP if local_status == "confirmed" else Decision.REVIEW
    if remote_status in {"failed", "cancelled", "customer_approval_denied", "charged_back"}:
        if local_status == "confirmed":
            return Decision.NOOP if reversal_recorded else Decision.REVERSE
        if local_status in {"created", "pending", "canceled"}:
            return Decision.FAIL
        return Decision.NOOP if local_status == "failed" else Decision.REVIEW
    return Decision.REVIEW
