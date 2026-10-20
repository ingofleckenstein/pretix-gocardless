from contextlib import nullcontext
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from pretix_gocardless.ticketing import release_pending, revoke_pending


class TicketingTests(TestCase):
    def setUp(self):
        self.order = SimpleNamespace(status="n", require_approval=False, pending_sum=Decimal("12.34"),
                                     valid_if_pending=False, save=Mock(), log_action=Mock())
        self.payment = SimpleNamespace(state="pending", amount=Decimal("12.34"), order_id=7)
        self.reference = SimpleNamespace(last_status="pending_submission", payment=self.payment,
                                         released_pending_tickets=False, save=Mock())
        order_model = SimpleNamespace(STATUS_PENDING="n", objects=Mock())
        order_model.objects.select_for_update.return_value.get.return_value = self.order
        reference_model = SimpleNamespace(objects=Mock())
        reference_model.objects.select_for_update.return_value.get.return_value = self.reference
        pretix = ModuleType("pretix.base.models")
        pretix.Order = order_model
        local = ModuleType("pretix_gocardless.models")
        local.PaymentReference = reference_model
        self.modules = patch.dict("sys.modules", {"pretix.base.models": pretix, "pretix_gocardless.models": local})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.atomic = patch("pretix_gocardless.ticketing.transaction.atomic", side_effect=nullcontext)
        self.atomic.start()
        self.addCleanup(self.atomic.stop)

    def test_release_preserves_pending_payment_and_can_be_revoked(self):
        self.assertTrue(release_pending(1))
        self.assertTrue(self.order.valid_if_pending)
        self.assertEqual(self.payment.state, "pending")
        self.assertTrue(revoke_pending(self.reference, self.payment))
        self.assertFalse(self.order.valid_if_pending)

    def test_existing_permission_is_not_claimed_or_revoked(self):
        self.order.valid_if_pending = True
        self.assertFalse(release_pending(1))
        self.assertFalse(revoke_pending(self.reference, self.payment))
        self.assertTrue(self.order.valid_if_pending)

    def test_incomplete_payment_or_approval_does_not_release(self):
        for change in ({"last_status": "pending_customer_approval"}, {"last_status": "failed"}):
            self.reference.last_status = change["last_status"]
            self.assertFalse(release_pending(1))
        self.reference.last_status = "submitted"
        self.order.require_approval = True
        self.assertFalse(release_pending(1))
        self.order.require_approval = False
        self.order.pending_sum = Decimal("20.00")
        self.assertFalse(release_pending(1))
