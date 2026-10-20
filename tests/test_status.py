import unittest

from pretix_gocardless.status import Decision, decide


class StatusTests(unittest.TestCase):
    def test_pending_is_not_paid(self):
        for status in ("pending_customer_approval", "pending_submission", "submitted"):
            with self.subTest(status=status):
                self.assertEqual(decide(status, "pending"), Decision.WAIT)

    def test_confirmation(self):
        self.assertEqual(decide("confirmed", "pending"), Decision.CONFIRM)
        self.assertEqual(decide("paid_out", "confirmed"), Decision.NOOP)

    def test_failure_and_late_failure_differ(self):
        self.assertEqual(decide("failed", "pending"), Decision.FAIL)
        self.assertEqual(decide("failed", "confirmed"), Decision.REVERSE)
        self.assertEqual(decide("charged_back", "confirmed"), Decision.REVERSE)

    def test_reversal_deduplication_and_conflicting_state(self):
        self.assertEqual(decide("charged_back", "confirmed", True), Decision.NOOP)
        self.assertEqual(decide("confirmed", "confirmed", True), Decision.REVIEW)

    def test_unknown_status_requires_review(self):
        self.assertEqual(decide("some_future_status", "pending"), Decision.REVIEW)
        self.assertEqual(decide("confirmed", "refunded"), Decision.REVIEW)


if __name__ == "__main__":
    unittest.main()
