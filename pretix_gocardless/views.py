"""Event-specific webhook endpoint; checkout remains disabled."""
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .webhooks import handle_delivery, process_reference


@csrf_exempt
@require_POST
def webhook(request, *args, **kwargs):
    from .payment import GoCardlessProvider

    provider = GoCardlessProvider(request.event)
    secret = provider.settings.get("webhook_secret")
    status = handle_delivery(
        request.body, request.headers.get("Webhook-Signature", ""), secret,
        lambda ref: process_reference(ref, request.event, provider),
    )
    return HttpResponse(status=status)
