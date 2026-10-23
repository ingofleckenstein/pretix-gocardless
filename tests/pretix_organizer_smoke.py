"""Run inside an initialized pretix shell, without modifying the database."""
from contextlib import nullcontext
from hashlib import sha256
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory
from pretix_gocardless.organizer_views import organizer_webhook

owner = SimpleNamespace(settings={"gocardless_sandbox_webhook_secret": "test-secret"})
payload = json.dumps({"events": [{"id": "EV123", "resource_type": "payments",
                                  "links": {"payment": "PM123"}}]}).encode()
signature = hmac.new(b"test-secret", payload, sha256).hexdigest()
factory = RequestFactory()

with patch("pretix_gocardless.organizer_views.get_object_or_404", return_value=owner), \
     patch("pretix_gocardless.organizer_views.scope", return_value=nullcontext()), \
     patch("pretix_gocardless.organizer_views.process_organizer_reference") as process:
    valid = factory.post("/", data=payload, content_type="application/json", HTTP_WEBHOOK_SIGNATURE=signature)
    assert organizer_webhook(valid, "test", "sandbox").status_code == 204
    assert process.call_count == 1
    invalid = factory.post("/", data=payload, content_type="application/json", HTTP_WEBHOOK_SIGNATURE="0" * 64)
    assert organizer_webhook(invalid, "test", "sandbox").status_code == 498
    assert process.call_count == 1
    assert organizer_webhook(valid, "test", "live").status_code == 503
    assert organizer_webhook(factory.get("/"), "test", "sandbox").status_code == 405
print("Organizer webhook HTTP smoke tests: OK")
