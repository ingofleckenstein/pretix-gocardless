"""Prefill a payer's chosen billing address without guessing missing data."""
import re


def prefilled_address(order):
    address = getattr(order, "invoice_address", None)
    if address is None and getattr(order, "customer_id", None):
        # More than one saved address requires an explicit choice in checkout.
        addresses = list(order.customer.stored_addresses.all()[:2])
        address = addresses[0] if len(addresses) == 1 else None
    if address is None:
        return {}

    result = {}
    lines = [line.strip() for line in (getattr(address, "street", "") or "").splitlines() if line.strip()]
    for index, line in enumerate(lines[:2], 1):
        result[f"address_line{index}"] = line
    if len(lines) > 2:
        result["address_line3"] = "\n".join(lines[2:])
    for local, remote in (("city", "city"), ("zipcode", "postal_code"), ("state", "region")):
        value = getattr(address, local, None)
        if value and str(value).strip():
            result[remote] = str(value).strip()
    country = str(getattr(address, "country", "") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{2}", country):
        result["country_code"] = country
    return result


def prefilled_customer(order):
    """Use purchaser details, never an attendee's potentially different identity."""
    result = prefilled_address(order)
    email = getattr(order, "email", None)
    if isinstance(email, str) and email.strip():
        result["email"] = email.strip()

    address = getattr(order, "invoice_address", None)
    parts = getattr(address, "name_parts", None) if address is not None else None
    # An explicitly selected billing name takes precedence over account data.
    if not parts and getattr(order, "customer_id", None):
        parts = getattr(order.customer, "name_parts", None)
    if isinstance(parts, dict):
        for field in ("given_name", "family_name"):
            value = parts.get(field)
            if isinstance(value, str) and value.strip():
                result[field] = value.strip()
    return result
