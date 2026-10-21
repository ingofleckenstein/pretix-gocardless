"""Keep credentials for the two GoCardless environments separate."""

ENVIRONMENTS = ("sandbox", "live")


class OrganizerAccountSettings:
    """Prefer organizer credentials per environment; keep legacy event fallback."""

    def __init__(self, organizer_settings, event_settings=None):
        self.organizer_settings = organizer_settings
        self.event_settings = event_settings or {}

    def get(self, key, default=None):
        for environment in ENVIRONMENTS:
            if key in {f"{environment}_access_token", f"{environment}_creditor_id",
                       f"{environment}_webhook_secret"}:
                prefix = f"gocardless_{environment}_"
                if self.organizer_settings.get(prefix + "access_token"):
                    return self.organizer_settings.get("gocardless_" + key, default)
                return self.event_settings.get(key, default)
        return self.event_settings.get(key, default)


def credential(settings, environment, name):
    if environment not in ENVIRONMENTS or name not in ("access_token", "webhook_secret", "creditor_id"):
        raise ValueError("Invalid GoCardless configuration")
    return settings.get(f"{environment}_{name}")
