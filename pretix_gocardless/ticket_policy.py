"""Experimental ticket policy, separate from payment confirmation.

This module is intentionally not connected to checkout yet. A caller must
authenticate and validate the current payment and own any validity flag it
changes; it must not revoke a flag set by an administrator or another plugin.
"""
from enum import Enum


class TicketDecision(Enum):
    WAIT = "wait"
    RELEASE = "release"
    REVOKE = "revoke"
    NOOP = "noop"
    REVIEW = "review"


def ticket_decision(policy, remote_status, local_payment_status, *, released_by_plugin=False):
    """Decide about ticket validity without claiming the debit was collected.

    Only an actually created payment in pending_submission or submitted can
    trigger early release. Awaiting customer approval is not sufficient.
    Unknown statuses and inconsistent combinations require human review.
    """
    if policy not in {"pending", "confirmed"}:
        return TicketDecision.REVIEW
    if remote_status in {"failed", "cancelled", "customer_approval_denied", "charged_back"}:
        return TicketDecision.REVOKE if released_by_plugin else TicketDecision.NOOP
    if remote_status in {"confirmed", "paid_out"}:
        return TicketDecision.NOOP  # Actual payment reconciliation handles confirmation.
    if remote_status == "pending_customer_approval":
        return TicketDecision.REVIEW if released_by_plugin else TicketDecision.WAIT
    if remote_status in {"pending_submission", "submitted"}:
        if local_payment_status not in {"created", "pending"}:
            return TicketDecision.REVIEW
        if released_by_plugin:
            return TicketDecision.NOOP
        return TicketDecision.RELEASE if policy == "pending" else TicketDecision.WAIT
    return TicketDecision.REVIEW
