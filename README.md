# pretix-gocardless

**Work in progress; experimental sandbox checkout only.** The provider can be
selected with sandbox credentials in an EUR event. A hosted authorisation and
simulated payment confirmation have been exercised in the GoCardless sandbox
with a local pretix instance. It is not production-ready: do not
install it on an operational pretix instance.

The current code contains an API client, event- and organizer-specific webhook
endpoints and unit tests. Sandbox webhook delivery has been exercised against a
public pretix installation. Full reconciliation, mandate events, refund
accounting and production integration tests remain incomplete. Do not rely on
this experimental implementation for operational payments.

The intended configuration offers either ticket release after a confirmed
payment or early release while direct debit is pending. Early release is a
business risk: a payment can fail or be charged back even after confirmation.
The experimental early-release setting uses pretix's pending-order validity;
it needs real pretix and sandbox integration testing. Issuing tickets early
must not falsely record an uncollected payment as paid. Refunds are to be
performed and confirmed manually; this plugin will not initiate refunds.

GoCardless direct debit is available for guest and signed-in checkouts.
The hosted mandate can permit multiple separate payments; it is not a subscription.

For signed-in customers, the plugin stores a GoCardless customer ID against
the pretix customer, scoped by creditor and environment. Later checkouts reuse
that customer ID. Code also attempts to reuse an eligible previously authorized
mandate for a signed-in customer after verifying customer, creditor, bank account,
environment and mandate state; otherwise a new hosted authorization is used.
One repeat checkout with a synthetic signed-in customer was observed on an
external sandbox installation. Broader repeat-checkout and concurrency scenarios
still need end-to-end validation.

The hosted Sandbox flow receives the order's billing address as editable
prefill. If the order has no billing address, the sole saved customer profile
address is used; multiple saved addresses are never guessed. Missing fields
are omitted, and the customer may edit the details at GoCardless.

The backend obtains creditors using the saved API token. A single creditor is
shown read-only; when several are returned, the organiser chooses one. Save a
new token and reopen the settings to load the creditors. The selection is
checked against the account again before checkout.

The backend contains separate Sandbox and Live credential fields and a
Sandbox/Live selector. Sandbox uses `api-sandbox.gocardless.com` and Live
uses `api.gocardless.com`. Live processing is deliberately disabled in this
experimental version; selecting Live does not enable live checkout.
Pretix test-mode events and persisted test orders always use Sandbox, even if
the selected environment is Live. This requires separate Sandbox credentials;
the Live token is never used for such orders.
An explicit development-only setting allows checkout experiments without a
webhook secret, exclusively while the event and resulting order are in pretix
test mode. Later payment status changes are not automatically received; this
does not make the integration suitable for production.
Use an event-specific endpoint ending in `gocardless/webhook/sandbox/` for
Sandbox and `gocardless/webhook/live/` for Live. A GoCardless webhook cannot
reach a URL that exists only on localhost; an externally reachable HTTPS URL
is required for end-to-end webhook testing. A change of the selected
environment does not change the environment of existing payments.
An experimental organizer settings page also displays organizer-scoped webhook
URLs and accepts separate GoCardless-generated secrets. Only sandbox dispatch is
implemented. Organizer routing retries unknown payment references (including
unrelated account activity), since early webhooks can precede local persistence;
it also retries unimplemented mandate events. This may cause repeated deliveries
and is not yet suitable for an operational server. The event-specific settings
and endpoint remain available for legacy sandbox testing. The organizer page
also supports API tokens and creditor selection; when an organizer token is
configured, it and its associated creditor are used instead of event credentials.

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
`reconcile.py` stores event IDs under a row lock and calls pretix
confirmation/failure methods. A chargeback or late failure after confirmation
creates a manual-review log and a configurable organizer notification, and is
acknowledged once recorded; it does **not** reverse the payment, reopen the
order, invalidate tickets or perform accounting. Organizers must monitor and
manually resolve these cases. Ambiguous states are retried. The new notification
and review workflow still require end-to-end testing, including email delivery.
Migration 0006 persists `manual_review_required` independently of the last API
snapshot, and preserves existing incidents where a confirmed payment's stored
status indicates a reversal. Later events cannot silently clear that flag or
replace the recorded adverse status. No accounting reversal is implied. Resolve
the financial and ticket consequences before an operator explicitly clears the
flag; there is deliberately no automatic reset.

Migration 0007 persists the Billing Request Flow identifier, without storing
its token-bearing authorisation URL. Retrying an interrupted checkout fetches
and validates that same flow while it is unexpired. An expired flow advances a
stored generation and receives a new purpose-separated idempotency key, so
concurrent retries converge on one replacement. Flows created by older plugin
versions cannot be recovered because their identifiers were never stored.

The unit suite covers mocked reconciliation and SQLite transactions using small
test models. `integration/run.py` additionally checks real pretix 2026.8 models
on a disposable PostgreSQL cluster, including simultaneous event replay. It
does not contact GoCardless or prove SMTP delivery, downloadable ticket content,
check-in behavior on a device, or compatibility with an operational deployment.

### Interrupted checkout and ticket release (sandbox development)

The provider settings offer an optional incomplete-authorisation reminder after
at least one hour. pretix's `runperiodic` must run regularly. The plugin checks
GoCardless before queuing mail and records separate customer/organizer deliveries;
the organizer copy is sent only when its address is configured. Both texts can
be edited per event. Mail queueing is not proof of delivery. Failed remote
lookups are retried on a later run. The customer order page offers a continuation
action only when the original billing request is still eligible.

Early ticket release is an explicit event-level setting: the order remains
unpaid but becomes valid for check-in after the debit has been requested. The
plugin revokes validity it granted if the debit fails before confirmation. Late
reversals require manual review. The email asks pretix to attach tickets, subject
to the event's ticket and mail settings. Verify this whole workflow with sandbox
orders before relying on it. This version does not support live payments.

Hosted flow URLs can expire; renewal of an expired flow has not yet been
verified. In that case do not initiate a duplicate payment: contact the
organizer. Webhooks, customer mail, periodic tasks, early ticket delivery and
failed-payment behavior require end-to-end testing on the target installation.

Documentation references:

- https://docs.pretix.eu/dev/development/api/payment.html
- https://docs.gocardless.com/docs/api-reference/billing-request
- https://docs.gocardless.com/docs/api-reference/billing-request-flow
- https://docs.gocardless.com/docs/api-reference/webhooks
- https://docs.gocardless.com/docs/api-reference/payment
- https://docs.gocardless.com/docs/collect-payments/one-off-payments/one-off-direct-debit
