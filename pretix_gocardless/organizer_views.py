"""Organizer-scoped webhook configuration and reception."""
from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import FormView
from django_scopes import scope
from urllib.parse import urljoin

from pretix.base.forms import SecretKeySettingsField, SettingsForm
from pretix.base.models import Organizer
from pretix.control.permissions import OrganizerPermissionRequiredMixin
from pretix.control.views.organizer import OrganizerDetailViewMixin

from .configuration import ENVIRONMENTS, OrganizerAccountSettings
from .creditors import creditor_field
from .organizer_routing import process_organizer_reference
from .webhooks import handle_delivery


class OrganizerWebhookForm(SettingsForm):
    gocardless_sandbox_access_token = SecretKeySettingsField(
        label=_("Sandbox API access token"), required=False,
        help_text=format_html('{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            _("Use a GoCardless sandbox token with read and write access."),
            "https://manage-sandbox.gocardless.com/developers/access-tokens", _("Open sandbox access tokens")))
    gocardless_sandbox_webhook_secret = SecretKeySettingsField(
        label=_("Sandbox webhook secret"), required=False,
        help_text=format_html('{} <a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            _("Create or rotate this secret in GoCardless, then enter it here."),
            "https://docs.gocardless.com/docs/getting-started/stay-up-to-date-with-webhooks",
            _("Webhook setup instructions")))
    gocardless_live_access_token = SecretKeySettingsField(
        label=_("Live API access token (not yet supported)"), required=False)
    gocardless_live_webhook_secret = SecretKeySettingsField(
        label=_("Live webhook secret (not yet supported)"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        account = OrganizerAccountSettings(self.obj.settings)
        for environment in ENVIRONMENTS:
            field = creditor_field(account, environment)
            name = f"gocardless_{environment}_creditor_" + ("display" if field.disabled else "id")
            self.fields[name] = field


class OrganizerWebhookSettings(OrganizerDetailViewMixin, OrganizerPermissionRequiredMixin, FormView):
    permission = "organizer.settings.general:write"
    template_name = "pretix_gocardless/organizer_settings.html"
    form_class = OrganizerWebhookForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["obj"] = self.request.organizer
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["webhook_urls"] = [(env, urljoin(settings.SITE_URL, reverse(
            "plugins:pretix_gocardless:organizer_webhook",
            kwargs={"organizer": self.request.organizer.slug, "environment": env})))
            for env in ENVIRONMENTS]
        return context

    def form_valid(self, form):
        form.save()
        return redirect(self.get_success_url())

    def get_success_url(self):
        return reverse("plugins:pretix_gocardless:organizer_settings",
                       kwargs={"organizer": self.request.organizer.slug})


@csrf_exempt
@require_POST
def organizer_webhook(request, organizer, environment):
    if environment not in ENVIRONMENTS:
        raise Http404("Unknown environment")
    if environment != "sandbox":
        return HttpResponse(status=503)
    owner = get_object_or_404(Organizer, slug=organizer)
    secret = owner.settings.get(f"gocardless_{environment}_webhook_secret")
    with scope(organizer=owner):
        status = handle_delivery(
            request.body, request.headers.get("Webhook-Signature", ""), secret,
            lambda ref: process_organizer_reference(ref, owner, environment),
        )
    return HttpResponse(status=status)
