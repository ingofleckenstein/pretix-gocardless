import unittest

from pretix_gocardless.ticket_policy import TicketDecision as D, ticket_decision


class TicketPolicyTests(unittest.TestCase):
    def test_early_release_only_after_payment_created(self):
        for status in ("pending_submission", "submitted"):
            with self.subTest(status=status):
                self.assertEqual(ticket_decision("pending", status, "pending"), D.RELEASE)
                self.assertEqual(ticket_decision("confirmed", status, "pending"), D.WAIT)
        self.assertEqual(ticket_decision("pending", "pending_customer_approval", "pending"), D.WAIT)

    def test_release_does_not_repeat_or_assert_payment_confirmation(self):
        self.assertEqual(ticket_decision("pending", "submitted", "pending", released_by_plugin=True), D.NOOP)
        self.assertEqual(ticket_decision("pending", "confirmed", "pending"), D.NOOP)

    def test_failure_only_revokes_plugin_owned_release(self):
        for status in ("failed", "cancelled", "charged_back", "customer_approval_denied"):
            with self.subTest(status=status):
                self.assertEqual(ticket_decision("pending", status, "pending", released_by_plugin=True), D.REVOKE)
                self.assertEqual(ticket_decision("pending", status, "pending"), D.NOOP)

    def test_unknown_and_inconsistent_statuses_require_review(self):
        self.assertEqual(ticket_decision("unknown", "submitted", "pending"), D.REVIEW)
        self.assertEqual(ticket_decision("pending", "new_status", "pending"), D.REVIEW)
        self.assertEqual(ticket_decision("pending", "submitted", "failed"), D.REVIEW)
        self.assertEqual(ticket_decision("pending", "pending_customer_approval", "pending", released_by_plugin=True), D.REVIEW)
