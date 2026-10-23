"""Resolve the creditor from the current environment's authenticated account."""
from django import forms
from django.utils.translation import gettext_lazy as _

from .client import Client, GoCardlessError
from .configuration import credential


class CreditorDisplayBoundField(forms.BoundField):
    @property
    def initial(self):
        return self.field.initial


class CreditorDisplayField(forms.CharField):
    def get_bound_field(self, form, field_name):
        return CreditorDisplayBoundField(form, self, field_name)


def available(settings, environment):
    token = credential(settings, environment, "access_token")
    return Client(token, environment).creditors() if token else []


def resolve(settings, environment):
    creditors = available(settings, environment)
    if len(creditors) == 1:
        return creditors[0][0]
    selected = credential(settings, environment, "creditor_id")
    if selected and selected in {item[0] for item in creditors}:
        return selected
    raise GoCardlessError("Choose an available creditor in the GoCardless settings")


def creditor_field(settings, environment):
    label = _("Sandbox creditor ID") if environment == "sandbox" else _("Live creditor ID (not yet enabled)")
    try:
        creditors = available(settings, environment)
    except GoCardlessError:
        return CreditorDisplayField(label=label, required=False, disabled=True,
                               help_text=_("Unable to fetch creditors. Check the saved API token and try again."))
    if len(creditors) == 1:
        return CreditorDisplayField(label=label, required=False, initial=creditors[0][0], disabled=True,
                               help_text=_("Automatically determined from the saved API token."))
    if len(creditors) > 1:
        return forms.ChoiceField(label=label, required=False,
                                 choices=[("", _("Please choose"))] + [
                                     (identifier, f"{name} ({identifier})") for identifier, name in creditors
                                 ], help_text=_("Choose the creditor for this event."))
    return CreditorDisplayField(label=label, required=False, disabled=True,
                           help_text=_("Save an API token to load the available creditors."))
