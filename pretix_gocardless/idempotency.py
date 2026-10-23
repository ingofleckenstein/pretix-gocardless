"""Stable, purpose-separated API keys derived from a persisted random UUID."""
from uuid import UUID, uuid5


def operation_key(creation_key, purpose, generation=0):
    if purpose not in {"mandate-request", "hosted-flow", "debit-payment"}:
        raise ValueError("Unknown operation purpose")
    if not isinstance(creation_key, UUID):
        raise ValueError("Expected a persisted UUID")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("Expected a non-negative operation generation")
    if purpose != "hosted-flow" and generation:
        raise ValueError("Only hosted flows can be renewed")
    suffix = purpose if generation == 0 else f"{purpose}:{generation}"
    return str(uuid5(creation_key, "pretix-gocardless:" + suffix))
