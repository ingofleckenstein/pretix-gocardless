import hashlib
import hmac
import re
import json


def verify_signature(body, signature, secret):
    if not isinstance(body, bytes) or not isinstance(signature, str) or not isinstance(secret, str) or not secret:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    return hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), signature)


class InvalidWebhook(ValueError):
    pass


def parse_webhook(body, signature, secret, max_bytes=1048576):
    """Authenticate before parsing; return events only, without processing them."""
    if not isinstance(body, bytes) or len(body) > max_bytes:
        raise InvalidWebhook("Invalid webhook body")
    if not verify_signature(body, signature, secret):
        raise InvalidWebhook("Invalid signature")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidWebhook("Malformed JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise InvalidWebhook("Invalid event collection")
    events = payload["events"]
    for event in events:
        if not isinstance(event, dict) or not re.fullmatch(r"EV[A-Za-z0-9]+", str(event.get("id", ""))):
            raise InvalidWebhook("Invalid event identifier")
    return events
