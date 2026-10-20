from pretix.multidomain import event_path

from .views import webhook, return_from_gocardless


event_patterns = [
    event_path("gocardless/webhook/<str:environment>/", webhook, name="webhook", require_live=False),
    event_path("gocardless/return/<str:order>/<str:hash>/<int:payment>/", return_from_gocardless,
               name="return", require_live=False),
]
