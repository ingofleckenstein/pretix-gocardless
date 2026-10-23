from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from django.utils import timezone
from pretix_gocardless.customer_status import CustomerStatus
from pretix_gocardless.reminders import send_for_reference


class ReminderTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.order = SimpleNamespace(status="n", require_approval=False, pending_sum=Decimal("20"),
            expires=now + timedelta(days=1), datetime=now - timedelta(hours=2),
            event=SimpleNamespace(name="Test", slug="test", organizer=SimpleNamespace(slug="organizer")), email="customer@example.org", locale="de",
            code="ABC", log_action=Mock())
        self.payment = SimpleNamespace(order_id=1, provider="gocardless", state="pending", amount=Decimal("20"))
        self.ref = SimpleNamespace(payment_id=1, environment="sandbox", billing_request_id="BR1", remote_payment_id=None)
        self.delivery = SimpleNamespace(customer_queued=None, organizer_queued=None, save=Mock())
        def model(value):
            result = SimpleNamespace(objects=Mock())
            result.objects.select_for_update.return_value.get.return_value = value
            return result
        base = ModuleType("pretix.base.models")
        base.Order = model(self.order)
        base.Order.STATUS_PENDING = "n"
        base.OrderPayment = model(self.payment)
        local = ModuleType("pretix_gocardless.models")
        local.PaymentReference = model(self.ref)
        local.ReminderDelivery = SimpleNamespace(objects=Mock())
        local.ReminderDelivery.objects.get_or_create.return_value = (self.delivery, True)
        mail = ModuleType("pretix.base.services.mail")
        self.mail = mail.mail = Mock(return_value=SimpleNamespace(log_data=Mock(return_value={})))
        provider = ModuleType("pretix_gocardless.payment")
        self.provider = SimpleNamespace(settings={"reminder_enabled": True,
            "reminder_organizer_email": "organizer@example.org"}, account_settings={},
            _environment=Mock(return_value="sandbox"))
        provider.GoCardlessProvider = Mock(return_value=self.provider)
        modules = patch.dict("sys.modules", {"pretix.base.models": base,
            "pretix.base.services.mail": mail, "pretix_gocardless.models": local,
            "pretix_gocardless.payment": provider})
        modules.start()
        self.addCleanup(modules.stop)
        for target, options in (("pretix_gocardless.reminders.transaction.atomic", {"side_effect": nullcontext}),
                                ("pretix_gocardless.reminders.settings", {"new": SimpleNamespace(SITE_URL="https://example.org/")}),
                                ("pretix_gocardless.configuration.credential", {"return_value": "test"}),
                                ("pretix_gocardless.client.Client", {"return_value": object()}),
                                ("pretix_gocardless.reminders.reverse", {"return_value": "/control/event/organizer/test/orders/ABC/"}),
                                ("pretix_gocardless.customer_status.assess", {"return_value": CustomerStatus.RESUME})):
            p = patch(target, **options)
            value = p.start()
            self.addCleanup(p.stop)
            if target.endswith("assess"):
                self.assess = value

    def test_both_recipients_are_queued_once(self):
        self.assertTrue(send_for_reference(1))
        self.assertEqual(self.mail.call_count, 2)
        self.assertIsNotNone(self.delivery.customer_queued)
        self.assertIsNotNone(self.delivery.organizer_queued)
        organizer_kwargs = self.mail.call_args.kwargs
        self.assertNotIn("order", organizer_kwargs)
        self.assertNotIn("position", organizer_kwargs)
        self.assertFalse(organizer_kwargs.get("plain_text_only", False))
        self.assertFalse(organizer_kwargs.get("no_order_links", False))
        self.assertFalse(organizer_kwargs["attach_tickets"])
        self.assertEqual(self.mail.call_args.args[3]["organizer_order_url"],
                         "https://example.org/control/event/organizer/test/orders/ABC/")
        self.assertNotIn("organizer_order_url", self.mail.call_args_list[0].args[3])
        self.assertFalse(send_for_reference(1))
        self.assertEqual(self.mail.call_count, 2)

    def test_no_reminder_when_remote_payment_is_processing(self):
        self.assess.return_value = CustomerStatus.PROCESSING
        self.assertFalse(send_for_reference(1))
        self.mail.assert_not_called()

    def test_no_reminder_for_recent_order_or_live_environment(self):
        self.order.datetime = timezone.now()
        self.assertFalse(send_for_reference(1))
        self.order.datetime -= timedelta(hours=2)
        self.ref.environment = "live"
        self.assertFalse(send_for_reference(1))
        self.mail.assert_not_called()

    def test_no_marker_if_pretix_does_not_queue_customer_mail(self):
        self.mail.side_effect = [None, SimpleNamespace(log_data=Mock(return_value={}))]
        send_for_reference(1)
        self.assertIsNone(self.delivery.customer_queued)
        self.assertIsNotNone(self.delivery.organizer_queued)
