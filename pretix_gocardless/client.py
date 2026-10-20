"""Small API client. Secrets are never included in exception messages."""
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlparse

import requests


class GoCardlessError(Exception):
    pass


class IdempotencyConflict(GoCardlessError):
    """A prior creation exists; the caller must fetch and validate it."""

    def __init__(self, resource_id):
        super().__init__("A resource already exists for this idempotency key")
        self.resource_id = resource_id


class Client:
    BASE = {"sandbox": "https://api-sandbox.gocardless.com", "live": "https://api.gocardless.com"}

    def __init__(self, token, environment, session=None):
        if not token or environment not in self.BASE:
            raise GoCardlessError("GoCardless configuration is incomplete")
        self.base = self.BASE[environment]
        self.token = token
        self.environment = environment
        self.session = session or requests.Session()

    def request(self, method, path, data=None, idempotency_key=None):
        if not path.startswith("/") or path.startswith("//"):
            raise GoCardlessError("Invalid API path")
        if method.upper() == "POST" and (not isinstance(idempotency_key, str) or not idempotency_key.strip()):
            raise GoCardlessError("A persistent idempotency key is required")
        headers = {"Authorization": "Bearer " + self.token, "GoCardless-Version": "2015-07-06", "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self.session.request(method, self.base + path, json=data, headers=headers, timeout=(5, 20), allow_redirects=False)
            if response.status_code == 409 and method.upper() == "POST":
                try:
                    errors = response.json()["error"]["errors"]
                    conflicts = [item["links"]["conflicting_resource_id"] for item in errors
                                 if item.get("reason") == "idempotent_creation_conflict"]
                except (ValueError, TypeError, KeyError, AttributeError):
                    conflicts = []
                if len(conflicts) == 1:
                    resource_id = conflicts[0]
                    prefixes = {"/billing_requests": "BRQ", "/billing_request_flows": "BRF", "/payments": "PM"}
                    prefix = prefixes.get(path)
                    if prefix is not None:
                        self._check_id(resource_id, prefix)
                        raise IdempotencyConflict(resource_id)
            if not 200 <= response.status_code < 300:
                raise GoCardlessError("GoCardless returned HTTP " + str(response.status_code))
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("Unexpected response")
            return value
        except (requests.RequestException, ValueError) as exc:
            raise GoCardlessError("GoCardless request failed") from exc

    def create_mandate_request(self, creditor_id, key):
        self._check_id(creditor_id, "CR")
        body = {"billing_requests": {"mandate_request": {"scheme": "sepa_core", "currency": "EUR"}, "links": {"creditor": creditor_id}}}
        return self.request("POST", "/billing_requests", body, key)["billing_requests"]

    def create_debit_payment(self, mandate_id, amount, key):
        self._check_id(mandate_id, "MD")
        try:
            amount = Decimal(amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise GoCardlessError("Invalid EUR amount") from exc
        cents = amount * 100
        if not amount.is_finite() or amount <= 0 or cents != cents.to_integral_value():
            raise GoCardlessError("Invalid EUR amount")
        body = {"payments": {"amount": int(cents), "currency": "EUR", "links": {"mandate": mandate_id}}}
        return self.request("POST", "/payments", body, key)["payments"]

    def create_flow(self, billing_id, return_url, key):
        body = {"billing_request_flows": {"links": {"billing_request": billing_id}, "redirect_uri": return_url, "exit_uri": return_url}}
        flow = self.request("POST", "/billing_request_flows", body, key)["billing_request_flows"]
        url = flow.get("authorisation_url", "")
        host = "pay-sandbox.gocardless.com" if self.environment == "sandbox" else "pay.gocardless.com"
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password:
            raise GoCardlessError("Invalid hosted flow URL")
        return flow

    def billing_request(self, object_id):
        self._check_id(object_id, "BRQ")
        return self.request("GET", "/billing_requests/" + object_id)["billing_requests"]

    def payment(self, object_id):
        self._check_id(object_id, "PM")
        return self.request("GET", "/payments/" + object_id)["payments"]

    def mandate(self, object_id):
        self._check_id(object_id, "MD")
        return self.request("GET", "/mandates/" + object_id)["mandates"]

    @staticmethod
    def _check_id(object_id, prefix):
        if not isinstance(object_id, str) or not re.fullmatch(prefix + r"[A-Za-z0-9]+", object_id):
            raise GoCardlessError("Invalid GoCardless object identifier")
