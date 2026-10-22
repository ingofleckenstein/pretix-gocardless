from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from pretix_gocardless.organizer_routing import process_organizer_reference, route_reference


class OrganizerRoutingTests(TestCase):
    def setUp(self):
        self.organizer = SimpleNamespace(pk=12)
        self.event = SimpleNamespace(organizer_id=12)
        self.payment = SimpleNamespace(environment="sandbox", payment=SimpleNamespace(
            order=SimpleNamespace(event=self.event)))
        self.reference = SimpleNamespace(resource_type="payments", resource_id="PM1")
        self.find = Mock(return_value=self.payment)
        self.process = Mock(return_value="done")

    def test_known_payment_routes_to_its_event(self):
        self.assertEqual(route_reference(self.reference, self.organizer, "sandbox", self.find, self.process), "done")
        self.find.assert_called_once_with(self.reference, self.organizer, "sandbox")
        self.process.assert_called_once_with(self.reference, self.event, "sandbox")

    def test_unknown_payment_is_retried_to_avoid_losing_early_webhooks(self):
        self.find.return_value = None
        with self.assertRaises(RuntimeError):
            route_reference(self.reference, self.organizer, "sandbox", self.find, self.process)
        self.process.assert_not_called()

    def test_wrong_organizer_or_environment_is_rejected(self):
        self.event.organizer_id = 13
        with self.assertRaises(RuntimeError):
            route_reference(self.reference, self.organizer, "sandbox", self.find, self.process)
        self.event.organizer_id = 12
        self.payment.environment = "live"
        with self.assertRaises(RuntimeError):
            route_reference(self.reference, self.organizer, "sandbox", self.find, self.process)
        self.process.assert_not_called()

    def test_known_mandate_is_routed_and_lookup_failure_is_not_acknowledged(self):
        self.reference.resource_type = "mandates"
        self.assertEqual(route_reference(self.reference, self.organizer, "sandbox", self.find, self.process), "done")
        self.process.assert_called_with(self.reference, self.event, "sandbox")
        self.find.side_effect = RuntimeError("database unavailable")
        with self.assertRaises(RuntimeError):
            route_reference(self.reference, self.organizer, "sandbox", self.find, self.process)

    def test_database_lookup_is_scoped_to_organizer_and_environment(self):
        models = ModuleType("pretix_gocardless.models")
        models.PaymentReference = SimpleNamespace(objects=Mock())
        query = models.PaymentReference.objects.select_related.return_value
        query.filter.return_value.first.return_value = self.payment
        payment = ModuleType("pretix_gocardless.payment")
        payment.GoCardlessProvider = Mock(return_value="provider")
        with patch.dict("sys.modules", {"pretix_gocardless.models": models,
                                        "pretix_gocardless.payment": payment}), \
                patch("pretix_gocardless.webhooks.process_reference", return_value="processed") as process:
            self.assertEqual(process_organizer_reference(self.reference, self.organizer, "sandbox"), "processed")
            query.filter.assert_called_once_with(remote_payment_id="PM1", environment="sandbox",
                                                 payment__order__event__organizer=self.organizer)
            process.assert_called_once_with(self.reference, self.event, "provider", "sandbox")
            self.reference.resource_type = "mandates"
            self.reference.resource_id = "MD1"
            query.reset_mock()
            self.assertEqual(process_organizer_reference(self.reference, self.organizer, "sandbox"), "processed")
            query.filter.assert_called_once_with(mandate_id="MD1", environment="sandbox",
                                                 payment__order__event__organizer=self.organizer)
