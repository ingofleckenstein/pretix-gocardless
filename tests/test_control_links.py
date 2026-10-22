from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from pretix_gocardless.control import payment_links, order_status


class ControlLinksTests(TestCase):
    def order(self, *payments):
        return SimpleNamespace(payments=Mock(filter=Mock(return_value=Mock(select_related=Mock(return_value=payments)))))

    def test_sandbox_and_live_use_the_correct_dashboard(self):
        sandbox = SimpleNamespace(full_id="A-P-1", gocardless_reference=SimpleNamespace(environment="sandbox", remote_payment_id="PM123"))
        live = SimpleNamespace(full_id="A-P-2", gocardless_reference=SimpleNamespace(environment="live", remote_payment_id="PM456"))
        result = str(payment_links(self.order(sandbox, live)))
        self.assertIn("https://manage-sandbox.gocardless.com/payments/PM123", result)
        self.assertIn("https://manage.gocardless.com/payments/PM456", result)
        self.assertIn("noopener noreferrer", result)

    def test_missing_or_invalid_references_are_not_linked(self):
        cases = [SimpleNamespace(full_id="A-P-1"),
                 SimpleNamespace(full_id="A-P-2", gocardless_reference=SimpleNamespace(environment="sandbox", remote_payment_id=None)),
                 SimpleNamespace(full_id="A-P-3", gocardless_reference=SimpleNamespace(environment="sandbox", remote_payment_id="PM123/../../evil")),
                 SimpleNamespace(full_id="A-P-4", gocardless_reference=SimpleNamespace(environment="other", remote_payment_id="PM123"))]
        self.assertEqual(payment_links(self.order(*cases)), "")

    def test_confirmed_chargeback_shows_manual_review_warning(self):
        payment = SimpleNamespace(full_id="A-P-1", state="confirmed", gocardless_reference=SimpleNamespace(
            environment="sandbox", remote_payment_id="PM123", last_status="charged_back"))
        self.assertIn("Manual review required", str(payment_links(self.order(payment))))

    def test_reconciled_payment_badges(self):
        payment = SimpleNamespace(full_id="A-P-1", gocardless_reference=SimpleNamespace(
            remote_payment_id="PM123", last_status="pending_submission"))
        for status, color in (("pending_submission", "warning"), ("submitted", "warning"),
                              ("confirmed", "success"), ("failed", "danger"),
                              ("charged_back", "danger")):
            payment.gocardless_reference.last_status = status
            self.assertIn("alert-" + color, str(order_status(self.order(payment))))
        payment.gocardless_reference.remote_payment_id = None
        self.assertEqual(str(order_status(self.order(payment))), "")

    def test_persisted_review_overrides_a_positive_status(self):
        payment = SimpleNamespace(full_id="A-P-1", state="confirmed", gocardless_reference=SimpleNamespace(
            environment="sandbox", remote_payment_id="PM123", last_status="confirmed", manual_review_required=True))
        self.assertIn("alert-danger", str(order_status(self.order(payment))))
        self.assertIn("Manual review required", str(payment_links(self.order(payment))))

    def test_inactive_mandate_is_visible_without_reversing_payment_badge(self):
        payment = SimpleNamespace(full_id="A-P-1", state="confirmed", gocardless_reference=SimpleNamespace(
            environment="sandbox", remote_payment_id="PM123", last_status="confirmed",
            mandate_status="cancelled", manual_review_required=False))
        result = str(order_status(self.order(payment)))
        self.assertIn("alert-success", result)
        self.assertIn("SEPA mandate is no longer usable", result)
