import unittest
from decimal import Decimal

from pretix_gocardless.client import Client, GoCardlessError, IdempotencyConflict
from pretix_gocardless.security import InvalidWebhook, parse_webhook, verify_signature


class Response:
    status_code = 201

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class Session:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.data)


class ClientTests(unittest.TestCase):
    def test_billing_request_is_eur_sepa_and_has_idempotency(self):
        session = Session({"billing_requests": {"id": "BRQ1"}})
        client = Client("token", "sandbox", session)
        self.assertEqual(client.create_mandate_request("CR123", "key"), {"id": "BRQ1"})
        _, url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api-sandbox.gocardless.com/billing_requests")
        self.assertNotIn("payment_request", kwargs["json"]["billing_requests"])
        self.assertEqual(kwargs["json"]["billing_requests"]["mandate_request"]["scheme"], "sepa_core")
        self.assertEqual(kwargs["json"]["billing_requests"]["links"]["creditor"], "CR123")
        self.assertEqual(kwargs["headers"]["Idempotency-Key"], "key")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (5, 20))

    def test_rejects_invalid_amount(self):
        client = Client("token", "sandbox", Session({}))
        for amount in ("0", "-1", "1.001", "NaN", "Infinity", "invalid", None):
            with self.subTest(amount=amount), self.assertRaises(GoCardlessError):
                client.create_debit_payment("MD123", amount, "key")

    def test_debit_payment_uses_mandate_and_eur_cents(self):
        session = Session({"payments": {"id": "PM1"}})
        client = Client("token", "sandbox", session)
        self.assertEqual(client.create_debit_payment("MD123", Decimal("12.34"), "key"), {"id": "PM1"})
        _, url, kwargs = session.calls[0]
        self.assertEqual(url, "https://api-sandbox.gocardless.com/payments")
        self.assertEqual(kwargs["json"]["payments"], {"amount": 1234, "currency": "EUR", "links": {"mandate": "MD123"}})
        self.assertEqual(kwargs["headers"]["Idempotency-Key"], "key")

    def test_mutating_requests_require_idempotency_key(self):
        session = Session({})
        client = Client("token", "sandbox", session)
        for key in (None, "", "  "):
            with self.subTest(key=key), self.assertRaises(GoCardlessError):
                client.create_debit_payment("MD123", "12.34", key)
        self.assertEqual(session.calls, [])

    def test_idempotency_conflict_exposes_only_validated_existing_id(self):
        payload = {"error": {"errors": [{"reason": "idempotent_creation_conflict", "links": {"conflicting_resource_id": "PM123"}}]}}
        response = Response(payload)
        response.status_code = 409
        session = Session({})
        session.request = lambda *args, **kwargs: response
        with self.assertRaises(IdempotencyConflict) as caught:
            Client("token", "sandbox", session).create_debit_payment("MD123", "12.34", "key")
        self.assertEqual(caught.exception.resource_id, "PM123")
        payload["error"]["errors"][0]["links"]["conflicting_resource_id"] = "PM123/../secret"
        with self.assertRaises(GoCardlessError) as caught:
            Client("token", "sandbox", session).create_debit_payment("MD123", "12.34", "key")
        self.assertNotIsInstance(caught.exception, IdempotencyConflict)

    def test_unrelated_or_malformed_conflict_does_not_offer_recovery(self):
        for payload in ({}, {"error": {"errors": [{"reason": "other"}]}}, {"error": {"errors": "bad"}}):
            response = Response(payload)
            response.status_code = 409
            session = Session({})
            session.request = lambda *args, **kwargs: response
            with self.subTest(payload=payload), self.assertRaises(GoCardlessError) as caught:
                Client("token", "sandbox", session).create_debit_payment("MD123", "12.34", "key")
            self.assertNotIsInstance(caught.exception, IdempotencyConflict)

    def test_rejects_hosted_url_to_other_origin(self):
        session = Session({"billing_request_flows": {"authorisation_url": "https://evil.example/"}})
        with self.assertRaises(GoCardlessError):
            Client("token", "sandbox", session).create_flow("BRQ1", "https://example.org/return", "key")

    def test_hosted_flow_keeps_currency_locked_and_fulfils(self):
        session = Session({"billing_request_flows": {"authorisation_url": "https://pay-sandbox.gocardless.com/billing/flow?id=example"}})
        Client("token", "sandbox", session).create_flow("BRQ1", "https://example.org/return", "key")
        flow = session.calls[0][2]["json"]["billing_request_flows"]
        self.assertTrue(flow["auto_fulfil"])
        self.assertTrue(flow["lock_currency"])

    def test_invalid_flow_id_or_port_is_rejected(self):
        session = Session({"billing_request_flows": {"authorisation_url": "https://pay-sandbox.gocardless.com:444/flow"}})
        client = Client("token", "sandbox", session)
        with self.assertRaises(GoCardlessError):
            client.create_flow("BRQ1/unsafe", "https://example.org/return", "key")
        self.assertEqual(session.calls, [])
        with self.assertRaises(GoCardlessError):
            client.create_flow("BRQ1", "https://example.org/return", "key")

    def test_rejects_incomplete_configuration(self):
        for token, env in (("", "sandbox"), ("x", "unknown")):
            with self.subTest(env=env), self.assertRaises(GoCardlessError):
                Client(token, env)

    def test_rejects_object_id_path_injection(self):
        client = Client("token", "sandbox", Session({}))
        for object_id in ("PM/../../bad", "PM1?x=1", "BRQ1", ""):
            with self.subTest(object_id=object_id), self.assertRaises(GoCardlessError):
                client.payment(object_id)

    def test_signature_example(self):
        import hashlib
        import hmac
        body = b'{"events":[]}'
        signature = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature(body, signature, "secret"))
        self.assertFalse(verify_signature(body + b" ", signature, "secret"))
        self.assertFalse(verify_signature(body, signature, ""))

    def test_parse_webhook_authenticates_and_validates(self):
        import hashlib
        import hmac
        body = b'{"events":[{"id":"EV123"}]}'
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertEqual(parse_webhook(body, sig, "secret"), [{"id": "EV123"}])
        with self.assertRaises(InvalidWebhook):
            parse_webhook(body, "0" * 64, "secret")
        with self.assertRaises(InvalidWebhook):
            parse_webhook(body, sig, "secret", max_bytes=1)

    def test_signed_malformed_payload_is_rejected(self):
        import hashlib
        import hmac
        for body in (b"{", b'[]', b'{"events":[{"id":"bad"}]}'):
            with self.subTest(body=body):
                sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
                with self.assertRaises(InvalidWebhook):
                    parse_webhook(body, sig, "secret")


if __name__ == "__main__":
    unittest.main()
