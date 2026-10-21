from django.dispatch import receiver
from pretix.base.signals import register_payment_providers, register_notification_types, periodic_task
from django_scopes import scopes_disabled


@receiver(register_payment_providers, dispatch_uid="pretix_gocardless_provider")
def register_provider(sender, **kwargs):
    from .payment import GoCardlessProvider
    return GoCardlessProvider


@receiver(periodic_task, dispatch_uid="pretix_gocardless_incomplete_authorisation_reminders")
@scopes_disabled()
def incomplete_authorisation_reminders(sender, **kwargs):
    from .reminders import run_reminders
    run_reminders()


@receiver(register_notification_types, dispatch_uid="pretix_gocardless_payment_failure_notification")
def register_payment_failure_notification(sender, **kwargs):
    from pretix.base.notifications import ParametrizedOrderNotificationType
    return ParametrizedOrderNotificationType(
        sender, "pretix_gocardless.event.order.payment_failed",
        _("GoCardless payment failed before confirmation"),
        _("GoCardless payment failed for order {order.code} — please review"),
    )


@receiver(register_notification_types, dispatch_uid="pretix_gocardless_payment_reversed_notification")
def register_payment_reversed_notification(sender, **kwargs):
    from pretix.base.notifications import ParametrizedOrderNotificationType
    return ParametrizedOrderNotificationType(
        sender, "pretix_gocardless.event.order.payment_reversed",
        _("GoCardless payment reversed after confirmation"),
        _("GoCardless payment reversed for order {order.code} — manual review required"),
    )


from pretix.control.signals import nav_organizer, order_info
from pretix.presale.signals import order_info_top as presale_order_info_top
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from pretix.base.logentrytypes import log_entry_types, OrderLogEntryType


@log_entry_types.new_from_dict({
    "pretix_gocardless.event.order.payment_failed": _("GoCardless payment {local_id} failed (status: {gocardless_status})."),
    "pretix_gocardless.event.order.payment_reversed": _("GoCardless payment {local_id} was reversed after confirmation (status: {gocardless_status}). The order and payment have not been changed; manual review is required."),
})
class GoCardlessOrderLogEntryType(OrderLogEntryType):
    pass


@receiver(order_info, dispatch_uid="pretix_gocardless_order_payment_links")
def order_payment_links(sender, order, request, **kwargs):
    from .control import order_status, payment_links
    from django.utils.html import format_html
    return format_html("{}{}", order_status(order), payment_links(order))


@receiver(presale_order_info_top, dispatch_uid="pretix_gocardless_customer_status")
def customer_payment_status(sender, order, request, **kwargs):
    from .control import order_status
    return order_status(order)


@receiver(nav_organizer, dispatch_uid="pretix_gocardless_organizer_navigation")
def organizer_navigation(sender, request, organizer, **kwargs):
    if not request.user.has_organizer_permission(organizer, "organizer.settings.general:write", request=request):
        return []
    return [{"label": _("GoCardless webhooks"),
             "url": reverse("plugins:pretix_gocardless:organizer_settings", kwargs={"organizer": organizer.slug}),
             "parent": reverse("control:organizer.edit", kwargs={"organizer": organizer.slug}),
             "active": request.resolver_match.url_name == "organizer_settings"}]
