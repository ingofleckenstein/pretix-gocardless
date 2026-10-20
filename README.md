# pretix-gocardless

**Work in progress, not usable for payments.** This is a fail-closed development
snapshot. The pretix provider is deliberately not selectable and never starts
a payment. Do not install it on an operational pretix instance.

The current code contains plugin discovery scaffolding, an initial API client,
an experimental event-specific webhook endpoint and unit tests. Complete
webhook processing, durable payment correlation, status reconciliation,
chargebacks, refunds, configuration and full integration tests are incomplete.
The database models and migrations are drafts only. The webhook endpoint retries
unresolved billing requests, mandate events and chargebacks instead of marking
them successful. It is not ready for use as a GoCardless webhook endpoint.
The API client and endpoint have not been tested against the real sandbox.

The intended SEPA sequence is a hosted mandate request followed by a separate
payment created against that mandate. A SEPA mandate is not automatically
limited to one payment. The future integration must use and explain it only
for the customer's authorised purchase, and handle notification requirements.
The code can derive distinct operation keys from a persisted random UUID and
detect an idempotency conflict, but this is not connected to the checkout and
cannot yet recover safely after ambiguous API responses. Callers must not use
this client to collect money.

## Installation examples (development only)

To install a local checkout into a disposable pretix Python environment:

```sh
python -m pip install ./pretix-gocardless
python -m pretix migrate
```

For a disposable Docker test container, mount the checkout and install it
inside the container. The installation disappears when the container is removed:

```sh
docker run --rm -it --entrypoint /bin/sh \
  -v "$PWD/pretix-gocardless:/plugin:ro" pretix/standalone:stable
# inside the container:
pip install /plugin
```

For a persistent custom image, keep the plugin checkout alongside this
Dockerfile and rebuild the image whenever its code changes:

```dockerfile
FROM pretix/standalone:stable
COPY pretix-gocardless /tmp/pretix-gocardless
RUN pip install /tmp/pretix-gocardless
```

These are illustrative development examples, not a tested deployment recipe;
the provider is deliberately unavailable. Do not put credentials in an image,
source checkout, Dockerfile or version control. Once the integration is ready,
follow the deployment's normal migration and restart procedure.

`status.py` contains experimental decision logic for current remote statuses.
`reconcile.py` is a draft transactional service called by the experimental endpoint:
it stores event IDs under a row lock and can call pretix confirmation/failure
methods. It requires creditor-account binding and integration tests. It refuses
to process chargebacks or ambiguous states pending a reviewed implementation.
The tests cover mocked reconciliation and SQLite transactions using small test
models. They are not pretix integration, PostgreSQL concurrency, hosted-flow,
or GoCardless sandbox tests.

Documentation references:

- https://docs.pretix.eu/dev/development/api/payment.html
- https://docs.gocardless.com/docs/api-reference/billing-request
- https://docs.gocardless.com/docs/api-reference/billing-request-flow
- https://docs.gocardless.com/docs/api-reference/webhooks
- https://docs.gocardless.com/docs/api-reference/payment
- https://docs.gocardless.com/docs/collect-payments/one-off-payments/one-off-direct-debit
