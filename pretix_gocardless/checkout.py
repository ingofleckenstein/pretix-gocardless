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


def locked_checkout_payment(payment):
    """Re-read the payment under a row lock before starting any remote intent.

    A request object can be stale while another worker cancels or confirms the
    payment. The lock must be held by the caller for the remote operation.
    """
    from pretix.base.models import Order, OrderPayment
    current = OrderPayment.objects.select_for_update().get(pk=payment.pk)
    order = Order.objects.select_for_update().get(pk=current.order_id)
    if (current.provider != "gocardless" or current.state not in {"created", "pending"}
            or current.amount != payment.amount or order.event.currency != "EUR"
            or getattr(order, "status", "n") != "n"):
        raise CheckoutMismatch("The payment is no longer eligible for collection")
    return current


def ensure_billing_request(client, creditor_id, creation_key, known_id=None, customer_id=None, bank_account_id=None):
    if known_id:
        billing_id = known_id
    else:
        try:
            args = (creditor_id, operation_key(creation_key, "mandate-request"))
            if bank_account_id:
                billing_id = client.create_mandate_request(*args, customer_id=customer_id, bank_account_id=bank_account_id)["id"]
            else:
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
            or (customer_id and links.get("customer") != customer_id)
            or (bank_account_id and links.get("customer_bank_account") != bank_account_id)):
        raise CheckoutMismatch("Billing request does not match the original mandate intent")
    return billing_id


def reusable_bank_account(client, billing_id, creditor_id, customer_id):
    """Return a verified reference, never the customer's bank details."""
    billing = client.billing_request(billing_id)
    links = billing.get("links") or {}
    if (billing.get("id") != billing_id or billing.get("status") != "fulfilled"
            or links.get("creditor") != creditor_id or links.get("customer") != customer_id):
        raise CheckoutMismatch("Previous billing request does not match this customer")
    bank_id = links.get("customer_bank_account")
    if not isinstance(bank_id, str) or not re.fullmatch(r"BA[A-Za-z0-9]+", bank_id):
        return None
    bank = client.customer_bank_account(bank_id)
    if (bank.get("id") != bank_id or (bank.get("links") or {}).get("customer") != customer_id):
        raise CheckoutMismatch("Bank account does not belong to this customer")
    return bank_id if bank.get("enabled") is True and bank.get("currency") == "EUR" else None


def previous_completed_reference(customer_id, environment, creditor_id, reference_id):
    from .models import PaymentReference
    return (PaymentReference.objects.filter(
        payment__order__customer_id=customer_id, environment=environment,
        creditor_id=creditor_id, remote_payment_id__isnull=False,
        billing_request_id__isnull=False,
    ).exclude(pk=reference_id).order_by("-created").first())


def verified_reusable_mandate(client, previous, creditor_id, customer_id):
    """Verify the original customer authorization and the current mandate."""
    from .validation import fulfilled_mandate_id, validate_mandate

    billing = client.billing_request(previous.billing_request_id)
    if (billing.get("links") or {}).get("customer") != customer_id:
        raise CheckoutMismatch("Previous mandate belongs to a different customer")
    mandate_id = fulfilled_mandate_id(billing, previous.billing_request_id, creditor_id)
    if previous.mandate_id != mandate_id:
        raise CheckoutMismatch("Previous mandate reference changed")
    bank_id = reusable_bank_account(client, previous.billing_request_id, creditor_id, customer_id)
    mandate = client.mandate(mandate_id)
    state = validate_mandate(mandate, mandate_id, creditor_id)
    if not bank_id or (mandate.get("links") or {}).get("customer_bank_account") != bank_id:
        return None
    return mandate_id if state in {"pending_submission", "submitted", "active"} else None


def start_reused_mandate(payment, client, environment, creditor_id):
    """Start one independent payment, or return False when a new mandate is needed.

    The mandate choice and idempotency key are committed before any debit API
    call; a retry must never silently switch to a different mandate.
    """
    if not payment.order.customer_id:
        return False
    from django.db import transaction
    from pretix.base.models import Customer
    from .models import CustomerLink, PaymentReference
    from .validation import validate_recovered_payment

    if transaction.get_connection().in_atomic_block:
        raise CheckoutMismatch("The payment intent must be committed before the API call")
    ref, _ = PaymentReference.objects.get_or_create(
        payment=payment, defaults={"environment": environment, "creditor_id": creditor_id},
    )
    with transaction.atomic():
        Customer.objects.select_for_update().get(pk=payment.order.customer_id)
        ref = PaymentReference.objects.select_for_update().get(pk=ref.pk)
        if ref.environment != environment or ref.creditor_id != creditor_id:
            raise CheckoutMismatch("Payment configuration changed")
        if ref.billing_request_id:
            return False
        payment = locked_checkout_payment(payment)
        link = CustomerLink.objects.filter(customer_id=payment.order.customer_id,
                                           environment=environment, creditor_id=creditor_id).first()
        previous = previous_completed_reference(payment.order.customer_id, environment, creditor_id, ref.pk)
        if not link or not previous:
            if ref.mandate_id or ref.remote_payment_id:
                raise CheckoutMismatch("Original mandate binding is missing")
            return False
        mandate_id = verified_reusable_mandate(client, previous, creditor_id, link.remote_customer_id)
        if ref.mandate_id and ref.mandate_id != mandate_id:
            raise CheckoutMismatch("Original mandate is no longer available")
        if not mandate_id:
            return False
        ref.mandate_id = mandate_id
        ref.save(update_fields=["mandate_id"])

    with transaction.atomic():
        ref = PaymentReference.objects.select_for_update().get(pk=ref.pk)
        payment = locked_checkout_payment(payment)
        if not ref.remote_payment_id:
            try:
                remote = client.create_debit_payment(ref.mandate_id, payment.amount,
                                                     operation_key(ref.creation_key, "debit-payment"))
                payment_id = remote.get("id")
            except IdempotencyConflict as exc:
                payment_id = exc.resource_id
        else:
            payment_id = ref.remote_payment_id
        remote = client.payment(payment_id)
        status = validate_recovered_payment(remote, payment_id, payment.amount, ref.mandate_id)
        ref.remote_payment_id = payment_id
        ref.last_status = status
        ref.save(update_fields=["remote_payment_id", "last_status"])
    return True


def ensure_hosted_flow(client, billing_id, creation_key, return_url, prefilled_customer=None):
    try:
        args = (billing_id, return_url, operation_key(creation_key, "hosted-flow"))
        flow = (client.create_flow(*args, prefilled_customer=prefilled_customer) if prefilled_customer
                else client.create_flow(*args))
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
        payment = locked_checkout_payment(payment)
        customer_link = (CustomerLink.objects.filter(customer_id=payment.order.customer_id,
                         environment=environment, creditor_id=creditor_id).first() if payment.order.customer_id else None)
        expected_customer = customer_link.remote_customer_id if customer_link else None
        bank_id = None
        if expected_customer and not reference.billing_request_id:
            previous = previous_completed_reference(payment.order.customer_id, environment, creditor_id, reference.pk)
            if previous:
                bank_id = reusable_bank_account(client, previous.billing_request_id, creditor_id, expected_customer)
        billing_id = ensure_billing_request(client, creditor_id, reference.creation_key,
                                            reference.billing_request_id, expected_customer, bank_id)
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
    from .addresses import prefilled_customer
    return ensure_hosted_flow(client, billing_id, reference.creation_key, return_url, prefilled_customer(payment.order))
