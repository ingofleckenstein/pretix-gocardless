"""Event-specific webhook endpoint; checkout remains disabled."""
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhooks import handle_delivery, process_reference
from .configuration import ENVIRONMENTS, credential


@require_POST
def resume_payment(request, order, hash, payment, *args, **kwargs):
    """Resume the same mandate intent, never create a new debit on this POST."""
    from django.utils import timezone
    from pretix.base.models import Order
    from pretix.multidomain.urlreverse import eventreverse
    from .client import Client
    from .customer_status import CustomerStatus, assess
    from .payment import GoCardlessProvider

    try:
        current_order = request.event.orders.get_with_secret_check(
            code=order, received_secret=hash.lower(), tag="plugins:pretix_gocardless:resume")
    except Order.DoesNotExist:
        raise Http404("Unknown order")
    current_payment = get_object_or_404(current_order.payments, pk=payment, provider="gocardless")
    fallback = eventreverse(request.event, "presale:event.order", kwargs={
        "order": current_order.code, "secret": current_order.secret})
    customer = getattr(request, "customer", None)
    if (current_order.customer_id and (not customer or customer.pk != current_order.customer_id)):
        messages.warning(request, _("Please sign in to the customer account associated with this order."))
        return redirect(fallback)
    if (current_order.status != Order.STATUS_PENDING or current_payment.state not in {"created", "pending"}
            or current_order.expires <= timezone.now() or current_order.pending_sum < current_payment.amount):
        messages.warning(request, _("This payment can no longer be resumed. Please contact the organizer."))
        return redirect(fallback)
    provider = GoCardlessProvider(request.event)
    try:
        ref = current_payment.gocardless_reference
        if provider._environment(current_order) != ref.environment or not provider._sandbox_ready(current_order):
            raise ValueError("Payment configuration is unavailable")
        client = Client(credential(provider.account_settings, ref.environment, "access_token"), ref.environment)
        if assess(ref, current_payment, client) is not CustomerStatus.RESUME:
            raise ValueError("Payment no longer awaiting authorisation")
        from .checkout import start_authorisation
        from pretix.multidomain.urlreverse import eventreverse_absolute
        return_url = eventreverse_absolute(request.event, "plugins:pretix_gocardless:return", kwargs={
            "order": current_order.code, "payment": current_payment.pk,
            "hash": current_order.tagged_secret("plugins:pretix_gocardless:return")})
        return redirect(start_authorisation(current_payment, client, ref.environment, ref.creditor_id, return_url))
    except Exception:
        messages.warning(request, _("The payment could not be resumed safely. Please try again later or contact the organizer."))
        return redirect(fallback)


@csrf_exempt
@require_POST
def webhook(request, environment, *args, **kwargs):
    from .payment import GoCardlessProvider

    if environment not in ENVIRONMENTS:
        raise Http404("Unknown environment")
    provider = GoCardlessProvider(request.event)
    secret = credential(provider.settings, environment, "webhook_secret")
    status = handle_delivery(
        request.body, request.headers.get("Webhook-Signature", ""), secret,
        lambda ref: process_reference(ref, request.event, provider, environment),
    )
    return HttpResponse(status=status)


def return_from_gocardless(request, order, hash, payment, *args, **kwargs):
    """Verify the order secret and independently fetch the remote result."""
    from pretix.base.models import Order
    from pretix.multidomain.urlreverse import eventreverse
    from .billing import process_billing
    from .client import Client
    from .models import PaymentReference
    from .payment import GoCardlessProvider

    try:
        current_order = request.event.orders.get_with_secret_check(
            code=order, received_secret=hash.lower(), tag="plugins:pretix_gocardless:return"
        )
    except Order.DoesNotExist:
        raise Http404("Unknown order")
    current_payment = get_object_or_404(current_order.payments, pk=payment, provider="gocardless")
    reference = get_object_or_404(PaymentReference, payment=current_payment)
    provider = GoCardlessProvider(request.event)
    try:
        if reference.environment != "sandbox":
            raise ValueError("Sandbox required")
        process_billing(reference.pk, "EVRETURN" + reference.creation_key.hex, "sandbox",
                        Client(credential(provider.account_settings, "sandbox", "access_token"), "sandbox"))
        if provider.settings.get("ticket_release") == "pending":
            from .ticketing import release_pending
            release_pending(reference.pk)
    except Exception:
        # The payer may have exited or the webhook may still be in flight.
        # Never reveal API responses or secrets in the message.
        messages.warning(request, _("Your direct debit authorisation is not complete yet. Please check your order."))
    return redirect(eventreverse(request.event, "presale:event.order", kwargs={
        "order": current_order.code, "secret": current_order.secret,
    }))
