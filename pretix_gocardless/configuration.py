"""Keep credentials for the two GoCardless environments separate."""

ENVIRONMENTS = ("sandbox", "live")


def credential(settings, environment, name):
    if environment not in ENVIRONMENTS or name not in ("access_token", "webhook_secret", "creditor_id"):
        raise ValueError("Invalid GoCardless configuration")
    return settings.get(f"{environment}_{name}")
