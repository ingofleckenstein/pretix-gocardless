from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PretixGoCardlessApp(AppConfig):
    name = "pretix_gocardless"
    verbose_name = "GoCardless"

    class PretixPluginMeta:
        name = "GoCardless (experimental)"
        author = "pretix-gocardless contributors"
        version = "0.1.0.dev0"
        category = "PAYMENT"
        description = _("Experimental integration. Do not use for real payments.")
        compatibility = "pretix>=2026.8.0"

    def ready(self):
        from . import signals  # noqa: F401
