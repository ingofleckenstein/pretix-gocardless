"""Disposable real-pretix/PostgreSQL checks; no external payment API calls.

Install pretix==2026.8.0, Django==5.2.17, pgserver and this plugin, then
run `python integration/run.py`. A fresh local PostgreSQL cluster is created
and stopped on exit. Never reads an existing pretix config or database.
"""
import configparser
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import os
import tempfile
from threading import Barrier
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4


@contextmanager
def disposable_database(root):
    """Own a fresh cluster, or a fresh DB on an explicitly supplied test server."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    dsn = os.environ.get("GC_TEST_POSTGRES_DSN")
    if dsn:
        # Never reuse or migrate a database supplied by the caller. Only the
        # newly created random database is modified, then dropped on exit.
        database = "gocardless_test_" + uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            try:
                params = conninfo_to_dict(dsn)
                params["dbname"] = database
                yield params
            finally:
                admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
    else:
        import pgserver
        pg = pgserver.get_server(root / "postgres", cleanup_mode="stop")
        try:
            yield conninfo_to_dict(pg.get_uri())
        finally:
            pg.cleanup()


def main():

    with tempfile.TemporaryDirectory(prefix="gocardless-integration-") as temporary:
        root = Path(temporary)
        with disposable_database(root) as params:
            config = configparser.ConfigParser()
            config["pretix"] = {"url": "http://localhost:8351", "datadir": str(root / "data")}
            config["django"] = {"secret": "disposable-integration-tests-only", "debug": "true"}
            config["database"] = {
                "backend": "postgresql", "name": params["dbname"],
                "user": params["user"], "host": params["host"],
                "port": params.get("port", "5432"),
            }
            if params.get("password"):
                config["database"]["password"] = params["password"]
            config["mail"] = {"backend": "console", "from": "test@example.invalid"}
            config_path = root / "pretix.cfg"
            with config_path.open("w") as output:
                config.write(output)
            # Explicit config prevents fallback to any operational installation.
            os.environ["PRETIX_CONFIG_FILE"] = str(config_path)
            os.environ["DJANGO_SETTINGS_MODULE"] = "pretix.settings"
            import django
            django.setup()
            from django.conf import settings
            from django.core.management import call_command
            from django.db import connection
            settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
            settings.CELERY_TASK_ALWAYS_EAGER = True
            call_command("migrate", interactive=False, verbosity=0)
            call_command("makemigrations", "pretix_gocardless", check=True, dry_run=True, verbosity=1)
            with connection.cursor() as cursor:
                cursor.execute("SHOW server_version")
                print("PostgreSQL", cursor.fetchone()[0])
            print("pretix", __import__("pretix").__version__, "Django", django.get_version())
            result = unittest.TextTestRunner(verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(IntegrationChecks))
            connection.close()
            if not result.wasSuccessful():
                raise SystemExit(1)


class IntegrationChecks(unittest.TestCase):
    def setUp(self):
        from django.utils.timezone import now
        from django_scopes import scopes_disabled
        from django.db import transaction
        from pretix.base.models import Customer, Event, Item, Order, OrderPayment, OrderPosition, Organizer, SalesChannel
        from pretix_gocardless.models import PaymentReference

        self.scope = scopes_disabled()
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)
        suffix = uuid4().hex
        organizer = Organizer.objects.create(slug="gc" + suffix, name="Synthetic integration fixture")
        self.event = Event.objects.create(organizer=organizer, slug="test", name="Sandbox fixture",
                                          date_from=now() + timedelta(days=7), currency="EUR", testmode=True)
        self.event.plugins = ["pretix_gocardless"]
        self.event.save()
        self.event.settings.set("invoice_generate", "False")
        self.event.settings.set("mail_sales_channel_placed_paid", [])
        self.customer = Customer.objects.create(organizer=organizer, email="test@example.invalid")
        with transaction.atomic():
            self.order = Order.objects.create(
                organizer=organizer, event=self.event, customer=self.customer, code="GCTEST",
                status=Order.STATUS_PENDING, total=Decimal("12.34"), datetime=now(),
                expires=now() + timedelta(days=3), locale="en", testmode=True,
                sales_channel=SalesChannel.objects.get(organizer=organizer, identifier="web"),
            )
            item = Item.objects.create(event=self.event, name="Synthetic ticket", default_price=Decimal("12.34"),
                                       admission=True)
            OrderPosition.objects.create(order=self.order, item=item, price=Decimal("12.34"))
            self.order.create_transactions()
        self.payment = OrderPayment.objects.create(order=self.order, state="pending", amount=Decimal("12.34"),
                                                    provider="gocardless")
        self.ref = PaymentReference.objects.create(
            payment=self.payment, environment="sandbox", creditor_id="CR123",
            mandate_id="MD" + suffix, billing_request_id="BRQ" + suffix, remote_payment_id="PM" + suffix,
            last_status="submitted",
        )
        self.remote = {"id": self.ref.remote_payment_id, "amount": 1234, "currency": "EUR", "status": "confirmed",
                       "links": {"mandate": self.ref.mandate_id}}

    def reconcile(self, event_id=None, remote=None):
        from pretix_gocardless.reconcile import reconcile
        return reconcile(self.ref.pk, event_id or "EV" + uuid4().hex, "sandbox", remote or self.remote,
                         mandate_id=self.ref.mandate_id, creditor_id=self.ref.creditor_id)

    def test_confirmed_policy_does_not_release_pending_tickets(self):
        self.order.refresh_from_db()
        self.assertFalse(self.order.valid_if_pending)
        self.assertEqual(self.order.status, "n")
        self.assertEqual(self.reconcile(), "confirm")
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.state, "confirmed")
        self.assertEqual(self.order.status, "p")

    def test_early_release_and_failure_use_real_pretix_state(self):
        from pretix.base.models import Order
        from pretix_gocardless.ticketing import release_pending
        # The mail method is verified at the boundary; no real mail is sent.
        with patch.object(Order, "send_mail") as send:
            self.assertTrue(release_pending(self.ref.pk))
            self.assertFalse(release_pending(self.ref.pk))
            send.assert_called_once()
            self.assertTrue(send.call_args.kwargs["attach_tickets"])
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertTrue(self.order.valid_if_pending)
        self.assertEqual(self.payment.state, "pending")
        self.assertEqual(self.order.status, "n")
        self.assertEqual(self.reconcile(remote={**self.remote, "status": "failed"}), "fail")
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertFalse(self.order.valid_if_pending)
        self.assertEqual(self.payment.state, "failed")

    def test_late_chargeback_is_durable_and_deduplicated(self):
        self.assertEqual(self.reconcile(), "confirm")
        self.assertEqual(self.reconcile(remote={**self.remote, "status": "charged_back"}), "reverse")
        self.assertEqual(self.reconcile(), "manual_review")
        self.assertEqual(self.reconcile(remote={**self.remote, "status": "charged_back"}), "manual_review")
        self.ref.refresh_from_db()
        self.order.refresh_from_db()
        self.assertTrue(self.ref.manual_review_required)
        self.assertEqual(self.ref.last_status, "charged_back")
        self.assertEqual(self.order.status, "p")
        self.assertEqual(self.order.all_logentries().filter(
            action_type="pretix_gocardless.event.order.payment_reversed").count(), 1)

    def test_simultaneous_event_replay_confirms_once(self):
        from django.db import close_old_connections, connections
        from django_scopes import scopes_disabled
        barrier = Barrier(2)
        event_id = "EV" + uuid4().hex
        def worker():
            close_old_connections()
            try:
                with scopes_disabled():
                    barrier.wait(timeout=10)
                    return self.reconcile(event_id=event_id)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertCountEqual(results, ["confirm", "duplicate"])
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.state, "confirmed")

    def test_canceled_payment_is_not_collected_by_a_stale_request(self):
        from pretix.base.models import OrderPayment
        from pretix_gocardless.checkout import CheckoutMismatch, start_authorisation
        self.ref.remote_payment_id = None
        self.ref.billing_request_id = None
        self.ref.save()
        OrderPayment.objects.filter(pk=self.payment.pk).update(state="canceled")
        client = Mock()
        with self.assertRaises(CheckoutMismatch):
            start_authorisation(self.payment, client, "sandbox", "CR123", "http://localhost:8351/return")
        client.create_mandate_request.assert_not_called()

    def test_simultaneous_repeat_checkout_creates_only_one_debit(self):
        from django.db import close_old_connections, connections
        from django_scopes import scopes_disabled
        from pretix.base.models import OrderPayment
        from pretix_gocardless.checkout import start_reused_mandate
        from pretix_gocardless.models import CustomerLink
        mandate_id = self.ref.mandate_id
        self.ref.billing_request_id = None
        self.ref.remote_payment_id = None
        self.ref.mandate_id = None
        self.ref.save()
        CustomerLink.objects.create(customer=self.customer, environment="sandbox", creditor_id="CR123",
                                    remote_customer_id="CU" + uuid4().hex)
        client = Mock()
        client.create_debit_payment.return_value = {"id": self.remote["id"]}
        client.payment.return_value = {**self.remote, "status": "pending_submission"}
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                with scopes_disabled():
                    payment = OrderPayment.objects.get(pk=self.payment.pk)
                    barrier.wait(timeout=10)
                    return start_reused_mandate(payment, client, "sandbox", "CR123")
            finally:
                connections.close_all()
        with patch("pretix_gocardless.checkout.previous_completed_reference", return_value=object()), \
                patch("pretix_gocardless.checkout.verified_reusable_mandate", return_value=mandate_id), \
                ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(results, [True, True])
        client.create_debit_payment.assert_called_once()
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.remote_payment_id, self.remote["id"])

    def test_first_hosted_authorisation_debit_and_confirmation(self):
        from pretix_gocardless.billing import process_billing
        from pretix_gocardless.checkout import start_authorisation
        billing_id, mandate_id = self.ref.billing_request_id, self.ref.mandate_id
        self.ref.billing_request_id = None
        self.ref.remote_payment_id = None
        self.ref.mandate_id = None
        self.ref.save()
        client = Mock()
        billing = {"id": billing_id, "status": "pending", "links": {
            "creditor": "CR123", "customer": "CU" + uuid4().hex},
            "mandate_request": {"scheme": "sepa_core", "currency": "EUR"}}
        client.create_mandate_request.return_value = {"id": billing_id}
        client.billing_request.return_value = billing
        return_url = "http://localhost:8351/return"
        client.create_flow.return_value = {"id": "BRF" + uuid4().hex, "links": {"billing_request": billing_id}, "redirect_uri": return_url,
            "exit_uri": return_url, "auto_fulfil": True, "lock_currency": True,
            "authorisation_url": "https://pay-sandbox.gocardless.com/synthetic",
            "expires_at": "2099-01-01T00:00:00Z"}
        client.validate_flow_url.side_effect = lambda url: url
        self.assertEqual(start_authorisation(self.payment, client, "sandbox", "CR123", return_url),
                         "https://pay-sandbox.gocardless.com/synthetic")
        billing["status"] = "fulfilled"
        billing["mandate_request"]["links"] = {"mandate": mandate_id}
        client.mandate.return_value = {"id": mandate_id, "status": "active", "scheme": "sepa_core",
                                       "links": {"creditor": "CR123"}}
        client.create_debit_payment.return_value = {"id": self.remote["id"]}
        client.payment.return_value = {**self.remote, "status": "pending_submission"}
        event_id = "EV" + uuid4().hex
        self.assertEqual(process_billing(self.ref.pk, event_id, "sandbox", client), "wait")
        self.assertEqual(process_billing(self.ref.pk, event_id, "sandbox", client), "duplicate")
        client.create_debit_payment.assert_called_once()
        self.ref.refresh_from_db()
        self.assertEqual(self.ref.remote_payment_id, self.remote["id"])
        self.assertEqual(self.reconcile(), "confirm")

    def test_cancelled_mandate_reconciles_current_failed_payment_once(self):
        from pretix_gocardless.events import EventReference
        from pretix_gocardless.mandates import process_mandate
        from pretix_gocardless.models import ProcessedEvent
        client = Mock()
        client.mandate.return_value = {
            "id": self.ref.mandate_id, "status": "cancelled", "scheme": "sepa_core",
            "links": {"creditor": self.ref.creditor_id},
        }
        client.payment.return_value = {**self.remote, "status": "failed"}
        event_id = "EV" + uuid4().hex
        reference = EventReference(event_id, "mandates", self.ref.mandate_id, action="cancelled")
        self.assertEqual(
            process_mandate(reference, self.event, "sandbox", client),
            "mandate_cancelled:fail",
        )
        self.assertEqual(process_mandate(reference, self.event, "sandbox", client), "duplicate")
        self.ref.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.ref.mandate_status, "cancelled")
        self.assertEqual(self.payment.state, "failed")
        self.assertEqual(ProcessedEvent.objects.filter(event_id=event_id).count(), 1)

    def test_cancelled_mandate_does_not_reverse_a_confirmed_payment(self):
        from pretix_gocardless.events import EventReference
        from pretix_gocardless.mandates import process_mandate
        self.assertEqual(self.reconcile(), "confirm")
        client = Mock()
        client.mandate.return_value = {
            "id": self.ref.mandate_id, "status": "cancelled", "scheme": "sepa_core",
            "links": {"creditor": self.ref.creditor_id},
        }
        client.payment.return_value = self.remote
        reference = EventReference(
            "EV" + uuid4().hex, "mandates", self.ref.mandate_id, action="cancelled",
        )
        self.assertEqual(
            process_mandate(reference, self.event, "sandbox", client),
            "mandate_cancelled:noop",
        )
        self.payment.refresh_from_db()
        self.ref.refresh_from_db()
        self.assertEqual(self.payment.state, "confirmed")
        self.assertEqual(self.ref.mandate_status, "cancelled")
        self.assertFalse(self.ref.manual_review_required)

    def test_migration_preserves_existing_confirmed_chargeback(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from pretix.base.models import OrderPayment
        self.ref.last_status = "charged_back"
        self.ref.save(update_fields=["last_status"])
        OrderPayment.objects.filter(pk=self.payment.pk).update(state="confirmed")
        MigrationExecutor(connection).migrate([("pretix_gocardless", "0005_reminderdelivery")])
        MigrationExecutor(connection).migrate([("pretix_gocardless", "0006_paymentreference_manual_review_required")])
        MigrationExecutor(connection).migrate([("pretix_gocardless", "0007_paymentreference_billing_request_flow")])
        MigrationExecutor(connection).migrate([("pretix_gocardless", "0008_paymentreference_mandate_status")])
        self.ref.refresh_from_db()
        self.assertTrue(self.ref.manual_review_required)
        self.assertEqual(self.ref.mandate_status, "")


if __name__ == "__main__":
    main()
