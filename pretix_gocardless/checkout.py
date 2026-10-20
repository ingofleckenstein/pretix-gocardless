"""Prepare a hosted authorisation for a persisted payment intent.

The caller must durably persist the creation UUID before any remote request
and save the returned billing request ID before starting the flow. This module
never creates a debit or marks an order paid.
"""
import re

from .client import IdempotencyConflict
from .idempotency import operation_key


class CheckoutMismatch(ValueError):
    pass


def ensure_billing_request(client, creditor_id, creation_key, known_id=None, customer_id=None):
    if known_id:
        billing_id = known_id
    else:
        try:
            args = (creditor_id, operation_key(creation_key, "mandate-request"))
            billing_id = client.create_mandate_request(*args, customer_id=customer_id)["id"] if customer_id else client.create_mandate_request(*args)["id"]
        except IdempotencyConflict as exc:
            billing_id = exc.resource_id
    if not isinstance(billing_id, str) or not re.fullmatch(r"BRQ[A-Za-z0-9]+", billing_id):
        raise CheckoutMismatch("Invalid billing request ID")
    billing = client.billing_request(billing_id)
    mandate = billing.get("mandate_request")
    links = billing.get("links")
    if (billing.get("id") != billing_id or billing.get("status") not in {"pending", "ready_to_fulfil"}
            or not isinstance(links, dict) or links.get("creditor") != creditor_id
            or not isinstance(mandate, dict) or mandate.get("scheme") != "sepa_core"
            or mandate.get("currency") != "EUR" or billing.get("payment_request") is not None
            or (customer_id and links.get("customer") != customer_id)):
        raise CheckoutMismatch("Billing request does not match the original mandate intent")
    return billing_id


def ensure_hosted_flow(client, billing_id, creation_key, return_url):
    try:
        flow = client.create_flow(billing_id, return_url, operation_key(creation_key, "hosted-flow"))
    except IdempotencyConflict as exc:
        flow = client.flow(exc.resource_id)
    links = flow.get("links")
    if (not isinstance(links, dict) or links.get("billing_request") != billing_id
            or flow.get("redirect_uri") != return_url or flow.get("exit_uri") != return_url
            or flow.get("auto_fulfil") is not True or flow.get("lock_currency") is not True):
        raise CheckoutMismatch("Hosted flow does not match the original intent")
    return client.validate_flow_url(flow.get("authorisation_url", ""))


def start_authorisation(payment, client, environment, creditor_id, return_url):
    """Create a persisted intent and return a hosted URL for that payment.

    Call outside an enclosing transaction so the random key is committed before
    contacting GoCardless. The reference row serializes concurrent attempts.
    """
    from django.db import transaction
    from pretix.base.models import Customer
    from .models import CustomerLink, PaymentReference

    if transaction.get_connection().in_atomic_block:
        raise CheckoutMismatch("The payment intent must be committed before the API call")
    reference, _ = PaymentReference.objects.get_or_create(
        payment=payment, defaults={"environment": environment, "creditor_id": creditor_id},
    )
    with transaction.atomic():
        if payment.order.customer_id:
            Customer.objects.select_for_update().get(pk=payment.order.customer_id)
        reference = PaymentReference.objects.select_for_update().get(pk=reference.pk)
        if reference.environment != environment or reference.creditor_id != creditor_id:
            raise CheckoutMismatch("Payment configuration changed")
        if reference.remote_payment_id:
            raise CheckoutMismatch("A debit is already associated with this payment")
        customer_link = (CustomerLink.objects.filter(customer_id=payment.order.customer_id,
                         environment=environment, creditor_id=creditor_id).first() if payment.order.customer_id else None)
        expected_customer = customer_link.remote_customer_id if customer_link else None
        billing_id = ensure_billing_request(client, creditor_id, reference.creation_key,
                                            reference.billing_request_id, expected_customer)
        billing = client.billing_request(billing_id)
        remote_customer = billing.get("links", {}).get("customer")
        if not isinstance(remote_customer, str) or not re.fullmatch(r"CU[A-Za-z0-9]+", remote_customer):
            raise CheckoutMismatch("Billing request is missing its customer")
        if expected_customer and remote_customer != expected_customer:
            raise CheckoutMismatch("Billing request has the wrong customer")
        if payment.order.customer_id and not customer_link:
            CustomerLink.objects.create(customer_id=payment.order.customer_id, environment=environment,
                                        creditor_id=creditor_id, remote_customer_id=remote_customer)
        reference.billing_request_id = billing_id
        reference.save(update_fields=["billing_request_id"])
    # The billing ID and its key have been committed before starting the flow.
    return ensure_hosted_flow(client, billing_id, reference.creation_key, return_url)

