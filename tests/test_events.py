from unittest import TestCase

from pretix_gocardless.events import EventReference, InvalidEvent, event_reference


class EventReferenceTests(TestCase):
    def test_normal_billing_progress_does_not_attempt_a_debit(self):
        for action in ("created", "flow_created", "flow_visited", "collect_customer_details", "collect_bank_account"):
            with self.subTest(action=action):
                self.assertIsNone(event_reference({"id": "EV123", "resource_type": "billing_requests",
                                                   "action": action, "links": {"billing_request": "BRQ123"}}))

    def test_fulfilled_billing_still_reconciles(self):
        event = {"id": "EV123", "resource_type": "billing_requests", "action": "fulfilled",
                 "links": {"billing_request": "BRQ123"}}
        self.assertEqual(event_reference(event), EventReference("EV123", "billing_requests", "BRQ123"))

    def test_created_mandate_is_acknowledged_without_changing_payment(self):
        self.assertIsNone(event_reference({"id": "EV12", "resource_type": "mandates",
                                           "action": "created", "links": {"mandate": "MD456"}}))

    def test_cancelled_mandate_remains_for_explicit_handling(self):
        event = {"id": "EV12", "resource_type": "mandates", "action": "cancelled",
                 "links": {"mandate": "MD456"}}
        self.assertEqual(event_reference(event), EventReference("EV12", "mandates", "MD456"))

    def test_normal_mandate_progress_does_not_block_payment_events(self):
        for action in ("submitted", "active"):
            with self.subTest(action=action):
                self.assertIsNone(event_reference({"id": "EV12", "resource_type": "mandates",
                                                   "action": action, "links": {"mandate": "MD456"}}))

    def test_failed_mandate_remains_for_explicit_handling(self):
        event = {"id": "EV12", "resource_type": "mandates", "action": "failed",
                 "links": {"mandate": "MD456"}}
        self.assertEqual(event_reference(event), EventReference("EV12", "mandates", "MD456"))

    def test_billing_request_with_mandate(self):
        event = {"id": "EV123", "resource_type": "billing_requests", "links": {"billing_request": "BRQ123", "mandate_request_mandate": "MD456"}}
        self.assertEqual(event_reference(event), EventReference("EV123", "billing_requests", "BRQ123", "MD456"))

    def test_payment_event(self):
        event = {"id": "EV12", "resource_type": "payments", "links": {"payment": "PM456"}}
        self.assertEqual(event_reference(event), EventReference("EV12", "payments", "PM456"))

    def test_unrelated_event(self):
        self.assertIsNone(event_reference({"id": "EV12", "resource_type": "customers"}))

    def test_mandate_events_are_not_silently_ignored(self):
        event = {"id": "EV12", "resource_type": "mandates", "links": {"mandate": "MD456"}}
        self.assertEqual(event_reference(event), EventReference("EV12", "mandates", "MD456"))

    def test_bad_links_rejected(self):
        for links in (None, {}, {"billing_request": "BRQ1/../../x"}, {"billing_request": "BRQ1", "mandate_request_mandate": "MD1?x"}):
            with self.subTest(links=links), self.assertRaises(InvalidEvent):
                event_reference({"id": "EV12", "resource_type": "billing_requests", "links": links})
