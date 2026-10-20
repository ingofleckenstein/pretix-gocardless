"""Stable, purpose-separated API keys derived from a persisted random UUID."""
from uuid import UUID, uuid5


def operation_key(creation_key, purpose):
    if purpose not in {"mandate-request", "hosted-flow", "debit-payment"}:
        raise ValueError("Unknown operation purpose")
    if not isinstance(creation_key, UUID):
        raise ValueError("Expected a persisted UUID")
    return str(uuid5(creation_key, "pretix-gocardless:" + purpose))
