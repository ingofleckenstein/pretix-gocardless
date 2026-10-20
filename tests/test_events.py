from unittest import TestCase

from pretix_gocardless.events import EventReference, InvalidEvent, event_reference


class EventReferenceTests(TestCase):
    def test_billing_request_with_mandate(self):
        event = {"id": "EV123", "resource_type": "billing_requests", "links": {"billing_request": "BRQ123", "mandate_request_mandate": "MD456"}}
        self.assertEqual(event_reference(event), EventReference("EV123", "billing_requests", "BRQ123", "MD456"))

    def test_payment_event(self):
        event = {"id": "EV12", "resource_type": "payments", "links": {"payment": "PM456"}}
        self.assertEqual(event_reference(event), EventReference("EV12", "payments", "PM456"))

    def test_unrelated_event(self):
        self.assertIsNone(event_reference({"id": "EV12", "resource_type": "mandates"}))

    def test_bad_links_rejected(self):
        for links in (None, {}, {"billing_request": "BRQ1/../../x"}, {"billing_request": "BRQ1", "mandate_request_mandate": "MD1?x"}):
            with self.subTest(links=links), self.assertRaises(InvalidEvent):
                event_reference({"id": "EV12", "resource_type": "billing_requests", "links": links})
