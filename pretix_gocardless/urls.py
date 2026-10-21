from pretix.multidomain import event_path
from django.urls import path

from .views import webhook, return_from_gocardless, resume_payment
from .organizer_views import OrganizerWebhookSettings, organizer_webhook

urlpatterns = [
    path("control/organizer/<str:organizer>/gocardless/", OrganizerWebhookSettings.as_view(), name="organizer_settings"),
    path("_gocardless/<str:organizer>/<str:environment>/webhook/", organizer_webhook, name="organizer_webhook"),
]


event_patterns = [
    event_path("gocardless/webhook/<str:environment>/", webhook, name="webhook", require_live=False),
    event_path("gocardless/return/<str:order>/<str:hash>/<int:payment>/", return_from_gocardless,
               name="return", require_live=False),
    event_path("gocardless/resume/<str:order>/<str:hash>/<int:payment>/", resume_payment,
               name="resume", require_live=False),
]
