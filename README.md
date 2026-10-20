# pretix-gocardless

**Work in progress; experimental sandbox checkout only.** The provider can be
selected with sandbox credentials in an EUR event. The complete flow has not
been tested against GoCardless or pretix. It is not production-ready: do not
install it on an operational pretix instance.

The current code contains plugin discovery scaffolding, an initial API client,
an experimental event-specific webhook endpoint and unit tests. Complete
webhook processing, durable payment correlation, status reconciliation,
chargebacks, refunds, configuration and full integration tests are incomplete.
The database models and migrations are drafts only. The webhook endpoint retries
unresolved billing requests, mandate events and chargebacks instead of marking
them successful. It is not ready for use as a GoCardless webhook endpoint.
The API client and endpoint have not been tested against the real sandbox.

The intended configuration offers either ticket release after a confirmed
payment or early release while direct debit is pending. Early release is a
business risk: a payment can fail or be charged back even after confirmation.
The experimental early-release setting uses pretix's pending-order validity;
it needs real pretix and sandbox integration testing. Issuing tickets early
must not falsely record an uncollected payment as paid. Refunds are to be
performed and confirmed manually; this plugin will not initiate refunds.

GoCardless direct debit is currently offered only to customers signed in to
their pretix customer account. Guest checkout does not offer this provider.
The hosted GoCardless mandate may be reusable even though this plugin creates
only one payment per order; the plugin does not yet reuse mandates across orders.

The backend contains separate Sandbox and Live credential fields and a
Sandbox/Live selector. Sandbox uses `api-sandbox.gocardless.com` and Live
uses `api.gocardless.com`. Live processing is deliberately disabled in this
experimental version; selecting Live does not enable live checkout.
Use an event-specific endpoint ending in `gocardless/webhook/sandbox/` for
Sandbox and `gocardless/webhook/live/` for Live. A GoCardless webhook cannot
reach a URL that exists only on localhost; an externally reachable HTTPS URL
is required for end-to-end webhook testing. A change of the selected
environment does not change the environment of existing payments.

The experimental `debit.py` component checks a fulfilled billing request and usable mandate,
uses a stable debit-creation key, and validates an independently retrieved
payment, including when recovering from an idempotency conflict. These helpers
do not replace persistent transactional orchestration or real sandbox tests.
The integration must only revoke ticket validity that it owns and must not
overwrite flags set by an organiser or other plugins.

`billing.py` contains an experimental transactional bridge from an event-bound
billing request to a debit. It locks the reference, recovers a remotely created
payment with the same idempotency key after an ambiguous response, validates
the payment and records the event transactionally. Billing execution through
the webhook is restricted to the sandbox. The database tests use SQLite test models; they do not establish
production concurrency behavior or validate actual GoCardless responses.

The intended SEPA sequence is a hosted mandate request followed by a separate
payment created against that mandate. A SEPA mandate is not automatically
limited to one payment. The future integration must use and explain it only
for the customer's authorised purchase, and handle notification requirements.
The code derives distinct operation keys from a persisted random UUID and
attempts to recover from idempotency conflicts. This behavior still requires
real sandbox and concurrency validation. Do not use it to collect real money.

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

These are illustrative development examples, not a tested deployment recipe.
Do not put credentials in an image,
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
