from pretix.multidomain import event_path

from .views import webhook


event_patterns = [event_path("gocardless/webhook/", webhook, name="webhook", require_live=False)]
