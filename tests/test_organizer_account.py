from unittest import TestCase

from pretix_gocardless.configuration import OrganizerAccountSettings, credential


class OrganizerAccountTests(TestCase):
    def test_organizer_account_precedes_legacy_without_mixing_credentials(self):
        organizer = {"gocardless_sandbox_access_token": "new", "gocardless_sandbox_creditor_id": "CR1"}
        event = {"sandbox_access_token": "old", "sandbox_creditor_id": "CR2",
                 "sandbox_webhook_secret": "legacy", "live_access_token": "old-live"}
        account = OrganizerAccountSettings(organizer, event)
        self.assertEqual(credential(account, "sandbox", "access_token"), "new")
        self.assertEqual(credential(account, "sandbox", "creditor_id"), "CR1")
        self.assertIsNone(credential(account, "sandbox", "webhook_secret"))
        self.assertEqual(credential(account, "live", "access_token"), "old-live")

    def test_fallback_is_per_environment_and_does_not_replace_event_settings(self):
        account = OrganizerAccountSettings({}, {"sandbox_access_token": "old", "ticket_release": "confirmed"})
        self.assertEqual(credential(account, "sandbox", "access_token"), "old")
        self.assertEqual(account.get("ticket_release"), "confirmed")
