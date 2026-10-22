import hashlib
import hmac
import json
from unittest import TestCase
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from django.test import RequestFactory

from pretix_gocardless.events import EventReference
from pretix_gocardless.views import webhook
from pretix_gocardless.webhooks import handle_delivery, process_reference


def signed(events):
    body = json.dumps({"events": events}).encode()
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return body, signature


class WebhookDeliveryTests(TestCase):
    def setUp(self):
        self.payment = {"id": "EV123", "resource_type": "payments", "links": {"payment": "PM123"}}
        self.handler = Mock()

    def test_valid_event_is_processed(self):
        body, signature = signed([self.payment])
        self.assertEqual(handle_delivery(body, signature, "test-secret", self.handler), 204)
        self.assertEqual(self.handler.call_args.args[0].resource_id, "PM123")

    def test_invalid_signature_never_calls_handler(self):
        body, signature = signed([self.payment])
        self.assertEqual(handle_delivery(body, signature, "wrong", self.handler), 498)
        self.handler.assert_not_called()

    def test_invalid_later_reference_never_partially_dispatches(self):
        body, signature = signed([self.payment, {"id": "EV456", "resource_type": "payments", "links": {}}])
        self.assertEqual(handle_delivery(body, signature, "test-secret", self.handler), 400)
        self.handler.assert_not_called()

    def test_handler_failure_requires_redelivery(self):
        body, signature = signed([self.payment])
        self.handler.side_effect = RuntimeError("sensitive data")
        self.assertEqual(handle_delivery(body, signature, "test-secret", self.handler), 503)

    def test_unrelated_events_are_ignored(self):
        body, signature = signed([{"id": "EV123", "resource_type": "customers"}])
        self.assertEqual(handle_delivery(body, signature, "test-secret", self.handler), 204)
        self.handler.assert_not_called()

    def test_batched_checkout_progress_is_acknowledged_without_debit(self):
        # A real sandbox delivery grouped these four progress events together.
        events = [{"id": f"EV{index}", "resource_type": "billing_requests",
                   "action": action, "links": {"billing_request": "BRQ123"}}
                  for index, action in enumerate(
                      ("created", "flow_created", "flow_visited", "collect_customer_details"), 1)]
        body, signature = signed(events)
        self.assertEqual(handle_delivery(body, signature, "test-secret", self.handler), 204)
        self.handler.assert_not_called()

    def test_progress_does_not_hide_a_failed_fulfilment(self):
        events = [{"id": "EV1", "resource_type": "billing_requests", "action": "created",
                   "links": {"billing_request": "BRQ123"}},
                  {"id": "EV2", "resource_type": "billing_requests", "action": "fulfilled",
                   "links": {"billing_request": "BRQ123"}}]
        self.handler.side_effect = RuntimeError("retry required")
        body, signature = signed(events)
        self.assertEqual(handle_delivery(body, signature, "test-secret", self.handler), 503)
        self.assertEqual(self.handler.call_count, 1)

    def test_payment_event_checks_account_before_reconciliation(self):
        ref = EventReference("EV123", "payments", "PM123")
        stored = SimpleNamespace(pk=5, environment="sandbox", mandate_id="MD123", creditor_id="CR123")
        module = ModuleType("pretix_gocardless.models")
        module.PaymentReference = SimpleNamespace(objects=Mock())
        module.PaymentReference.objects.get.return_value = stored
        client = Mock()
        client.payment.return_value = {"id": "PM123"}
        client.mandate.return_value = {"id": "MD123", "scheme": "sepa_core", "status": "active", "links": {"creditor": "CR123"}}
        provider = SimpleNamespace(settings={"environment": "live", "sandbox_access_token": "test-token", "live_access_token": "other-token"})
        with patch.dict("sys.modules", {"pretix_gocardless.models": module}), \
                patch("pretix_gocardless.client.Client", return_value=client) as client_factory, \
                patch("pretix_gocardless.reconcile.reconcile") as reconciler:
            process_reference(ref, "test-event", provider, "sandbox")
            client_factory.assert_called_once_with("test-token", "sandbox")
            args, kwargs = reconciler.call_args
            self.assertEqual(args[:3], (5, "EV123", "sandbox"))
            self.assertEqual(args[3](), {"id": "PM123"})
            self.assertEqual(kwargs, {"mandate_id": "MD123", "creditor_id": "CR123"})
            client.mandate.return_value["links"]["creditor"] = "CR999"
            reconciler.reset_mock()
            with self.assertRaises(ValueError):
                process_reference(ref, "test-event", provider, "sandbox")
            reconciler.assert_not_called()

    def test_later_payment_event_can_trigger_early_release(self):
        ref = EventReference("EV123", "payments", "PM123")
        stored = SimpleNamespace(pk=5, environment="sandbox", mandate_id="MD123", creditor_id="CR123")
        module = ModuleType("pretix_gocardless.models")
        module.PaymentReference = SimpleNamespace(objects=Mock())
        module.PaymentReference.objects.get.return_value = stored
        client = Mock()
        client.payment.return_value = {"id": "PM123"}
        client.mandate.return_value = {"id": "MD123", "scheme": "sepa_core", "status": "active",
                                       "links": {"creditor": "CR123"}}
        provider = SimpleNamespace(settings={"sandbox_access_token": "token", "ticket_release": "pending"})
        with patch.dict("sys.modules", {"pretix_gocardless.models": module}), \
                patch("pretix_gocardless.client.Client", return_value=client), \
                patch("pretix_gocardless.reconcile.reconcile", return_value="wait"), \
                patch("pretix_gocardless.ticketing.release_pending") as release:
            self.assertEqual(process_reference(ref, "test-event", provider, "sandbox"), "wait")
            release.assert_called_once_with(5)

    def test_sandbox_signature_is_not_accepted_for_live(self):
        module = ModuleType("pretix_gocardless.payment")
        module.GoCardlessProvider = Mock(return_value=SimpleNamespace(settings={
            "sandbox_webhook_secret": "test-secret", "live_webhook_secret": "live-secret",
        }))
        body, signature = signed([])
        factory = RequestFactory()
        request = factory.post("/gocardless/webhook/live/", data=body,
                               content_type="application/json", HTTP_WEBHOOK_SIGNATURE=signature)
        request.event = object()
        with patch.dict("sys.modules", {"pretix_gocardless.payment": module}):
            self.assertEqual(webhook(request, "live").status_code, 498)
            self.assertEqual(webhook(request, "sandbox").status_code, 204)

    def test_billing_reference_is_scoped_to_the_event(self):
        module = ModuleType("pretix_gocardless.models")
        module.PaymentReference = SimpleNamespace(objects=Mock())
        module.PaymentReference.objects.get.return_value = SimpleNamespace(pk=5, environment="sandbox")
        provider = SimpleNamespace(settings={"environment": "sandbox", "sandbox_access_token": "test-token"})
        with patch.dict("sys.modules", {"pretix_gocardless.models": module}), \
                patch("pretix_gocardless.client.Client") as client, \
                patch("pretix_gocardless.billing.process_billing", return_value="wait") as process:
            self.assertEqual(process_reference(EventReference("EV123", "billing_requests", "BRQ123"), "event", provider, "sandbox"), "wait")
            module.PaymentReference.objects.get.assert_called_once_with(billing_request_id="BRQ123", payment__order__event="event")
            process.assert_called_once_with(5, "EV123", "sandbox", client.return_value)

    def test_billing_execution_requires_sandbox(self):
        with self.assertRaises(RuntimeError):
            process_reference(EventReference("EV123", "billing_requests", "BRQ123"), None, None, "live")

    def test_http_endpoint_rejects_invalid_signature_and_requires_post(self):
        module = ModuleType("pretix_gocardless.payment")
        module.GoCardlessProvider = Mock(return_value=SimpleNamespace(settings={"sandbox_webhook_secret": "test-secret"}))
        factory = RequestFactory()
        request = factory.post("/gocardless/webhook/", data=b'{"events":[]}', content_type="application/json", HTTP_WEBHOOK_SIGNATURE="bad")
        request.event = object()
        with patch.dict("sys.modules", {"pretix_gocardless.payment": module}):
            self.assertEqual(webhook(request, "sandbox").status_code, 498)
            self.assertEqual(webhook(factory.get("/gocardless/webhook/"), "sandbox").status_code, 405)

    def test_http_endpoint_acknowledges_handled_mandate(self):
        module = ModuleType("pretix_gocardless.payment")
        module.GoCardlessProvider = Mock(return_value=SimpleNamespace(settings={"sandbox_webhook_secret": "test-secret"}))
        body, signature = signed([{"id": "EV123", "resource_type": "mandates", "links": {"mandate": "MD123"}}])
        request = RequestFactory().post("/gocardless/webhook/", data=body, content_type="application/json", HTTP_WEBHOOK_SIGNATURE=signature)
        request.event = object()
        with patch.dict("sys.modules", {"pretix_gocardless.payment": module}), \
                patch("pretix_gocardless.views.process_reference", return_value="mandate_cancelled"):
            self.assertEqual(webhook(request, "sandbox").status_code, 204)
