from django.dispatch import receiver
from pretix.base.signals import register_payment_providers


@receiver(register_payment_providers, dispatch_uid="pretix_gocardless_provider")
def register_provider(sender, **kwargs):
    from .payment import GoCardlessProvider
    return GoCardlessProvider
