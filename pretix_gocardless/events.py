"""Interpret authenticated event references without changing payment state."""
import re
from dataclasses import dataclass


class InvalidEvent(ValueError):
    pass


@dataclass(frozen=True)
class EventReference:
    event_id: str
    resource_type: str
    resource_id: str
    related_mandate_id: str | None = None
    action: str | None = None


def event_reference(event):
    """Extract IDs from a signature-verified event, or None for unrelated types.

    A payment event can arrive before its billing-request event. The caller must
    retry or reconcile later rather than acknowledge an unresolvable event.
    """
    if not isinstance(event, dict):
        raise InvalidEvent("Event must be an object")
    event_id = event.get("id")
    if not isinstance(event_id, str) or not re.fullmatch(r"EV[A-Za-z0-9]+", event_id):
        raise InvalidEvent("Invalid event identifier")
    kind = event.get("resource_type")
    if kind not in {"billing_requests", "payments", "mandates"}:
        return None
    links = event.get("links")
    if not isinstance(links, dict):
        raise InvalidEvent("Missing event links")
    key, pattern = {
        "billing_requests": ("billing_request", r"BRQ[A-Za-z0-9]+"),
        "payments": ("payment", r"PM[A-Za-z0-9]+"),
        "mandates": ("mandate", r"MD[A-Za-z0-9]+"),
    }[kind]
    object_id = links.get(key)
    if not isinstance(object_id, str) or not re.fullmatch(pattern, object_id):
        raise InvalidEvent("Invalid resource identifier")
    action = event.get("action")
    if action is not None and (not isinstance(action, str) or not re.fullmatch(r"[a-z_]+", action)):
        raise InvalidEvent("Invalid event action")
    # Normal mandate progress does not decide whether a debit was collected;
    # payment events reconcile that separately. Adverse mandate events are
    # passed on so their status can be stored and linked debits checked.
    if kind == "mandates" and action in {"created", "submitted", "active"}:
        return None
    # These are progress notifications, not authorisation. Processing them as
    # fulfilled requests creates avoidable 503 retries while the payer is
    # still entering their details. The fulfilled event triggers the debit.
    if kind == "billing_requests" and event.get("action") in {
        "created", "flow_created", "flow_visited", "collect_customer_details", "collect_bank_account",
    }:
        return None
    mandate_id = links.get("mandate_request_mandate") if kind == "billing_requests" else None
    if mandate_id is not None and (not isinstance(mandate_id, str) or not re.fullmatch(r"MD[A-Za-z0-9]+", mandate_id)):
        raise InvalidEvent("Invalid related mandate identifier")
    return EventReference(
        event_id, kind, object_id, mandate_id,
        action if kind == "mandates" else None,
    )
