"""Validate the remote payment against a locally stored payment reference."""
from decimal import Decimal, InvalidOperation
import re


class PaymentMismatch(ValueError):
    pass


def fulfilled_mandate_id(billing_request, expected_id, expected_creditor):
    """Extract a mandate only from a fulfilled, correctly bound billing request."""
    if (not isinstance(expected_id, str) or not re.fullmatch(r"BRQ[A-Za-z0-9]+", expected_id)
            or not isinstance(expected_creditor, str) or not re.fullmatch(r"CR[A-Za-z0-9]+", expected_creditor)):
        raise PaymentMismatch("Invalid billing request or creditor reference")
    if not isinstance(billing_request, dict) or billing_request.get("id") != expected_id or billing_request.get("status") != "fulfilled":
        raise PaymentMismatch("Billing request identity or status mismatch")
    links = billing_request.get("links")
    mandate = billing_request.get("mandate_request")
    if not isinstance(links, dict) or links.get("creditor") != expected_creditor:
        raise PaymentMismatch("Billing request creditor mismatch")
    if not isinstance(mandate, dict) or mandate.get("scheme") != "sepa_core" or mandate.get("currency") != "EUR":
        raise PaymentMismatch("Billing request scheme or currency mismatch")
    mandate_links = mandate.get("links")
    mandate_id = mandate_links.get("mandate") if isinstance(mandate_links, dict) else None
    if not isinstance(mandate_id, str) or not re.fullmatch(r"MD[A-Za-z0-9]+", mandate_id):
        raise PaymentMismatch("Missing mandate identifier")
    return mandate_id


def validate_remote_payment(remote, expected_id, expected_amount, expected_currency="EUR"):
    """Reject wrong or malformed objects before making any state change.

    Account and environment binding must additionally be enforced by the caller.
    """
    if not isinstance(remote, dict) or not isinstance(expected_id, str) or not re.fullmatch(r"PM[A-Za-z0-9]+", expected_id):
        raise PaymentMismatch("Invalid payment reference")
    if remote.get("id") != expected_id or remote.get("currency") != expected_currency or expected_currency != "EUR":
        raise PaymentMismatch("Payment identity or currency mismatch")
    amount = remote.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise PaymentMismatch("Invalid remote amount")
    try:
        expected = Decimal(expected_amount)
        if not expected.is_finite() or expected <= 0 or expected * 100 != amount:
            raise PaymentMismatch("Payment amount mismatch")
    except (InvalidOperation, TypeError, ValueError) as exc:
        if isinstance(exc, PaymentMismatch):
            raise
        raise PaymentMismatch("Invalid local amount") from exc
    if not isinstance(remote.get("status"), str):
        raise PaymentMismatch("Missing status")
    return remote["status"]


def validate_recovered_payment(remote, expected_id, expected_amount, expected_mandate):
    """Validate a fetched conflict against the original immutable intent.

    Environment, creditor and the intent itself must also be bound by the
    caller before this function can safely be used by an active checkout.
    """
    if not isinstance(expected_mandate, str) or not re.fullmatch(r"MD[A-Za-z0-9]+", expected_mandate):
        raise PaymentMismatch("Invalid mandate reference")
    status = validate_remote_payment(remote, expected_id, expected_amount)
    links = remote.get("links")
    if not isinstance(links, dict) or links.get("mandate") != expected_mandate:
        raise PaymentMismatch("Payment mandate mismatch")
    return status


def validate_mandate(remote, expected_id, expected_creditor):
    """Verify a fetched mandate belongs to the expected creditor and scheme.

    Returning the status does not authorise collection; the caller must also
    verify the billing request, customer consent and usable mandate state.
    """
    if (not isinstance(expected_id, str) or not re.fullmatch(r"MD[A-Za-z0-9]+", expected_id)
            or not isinstance(expected_creditor, str) or not re.fullmatch(r"CR[A-Za-z0-9]+", expected_creditor)):
        raise PaymentMismatch("Invalid mandate or creditor reference")
    if not isinstance(remote, dict) or remote.get("id") != expected_id or remote.get("scheme") != "sepa_core":
        raise PaymentMismatch("Mandate identity or scheme mismatch")
    links = remote.get("links")
    if not isinstance(links, dict) or links.get("creditor") != expected_creditor:
        raise PaymentMismatch("Mandate creditor mismatch")
    if not isinstance(remote.get("status"), str):
        raise PaymentMismatch("Missing mandate status")
    return remote["status"]
