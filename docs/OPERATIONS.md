# Sandbox deployment and release gates

This is still an experimental sandbox-only build. No real payment is authorized
by this document. The deployment example has not been built against the target
server's custom image.

## Before changing a deployment

1. Inspect the actual Compose services, image IDs/digests, image runtime user,
   pretix/Python/Django versions and plugin version. Do not print environment
   variables, Compose config containing credentials, tokens or webhook bodies.
2. Back up the database and retain the exact current image. Verify that the
   backup can be restored in an isolated environment. Restrict backup access.
3. Execute `scripts/server_preflight.py` in the target pretix shell. It is read
   only and reports versions and migration names, without customer data.
4. Test the exact wheel and migrations on a disposable restore. A package
   installation in a running container is temporary and is lost on recreation.
5. Build a new immutable image with the wheel. `deployment/Dockerfile` requires
   the actual pinned base image and runtime user as build arguments; do not
   guess these or replace an existing Compose setup blindly. The wheel adds no
   dependencies beyond requests, already provided by pretix.
6. In a maintenance window, stop accepting new GoCardless checkouts, retain
   incoming webhook retries, run migrations once using the new image, then
   restart all application and background workers from that same image.
7. Run the preflight again. Verify HTTPS reachability, a signed sandbox event,
   its actual payment effect, periodic execution and mail queue processing.
   HTTP 204 by itself is insufficient evidence of successful reconciliation.

Migration 0006 adds a persistent manual-review flag and backfills incidents
whose stored status is adverse while the pretix payment is confirmed. It does
not create refunds or reopen paid orders. It cannot reconstruct incident data
that was already overwritten before this migration.

## Rollback

Retain the database backup and prior image together. Do not reverse 0006 during
normal rollback: that discards incident flags. The previous application can run
with an unused extra database column, but it will not preserve the new review
semantics. Suspend this provider until forward recovery is verified. Restoring
a database backup can lose references to debits created after the backup;
reconcile these with GoCardless before resuming collections. Never create new
debits merely to repair missing references.

## Operational checks

- Monitor failed webhook deliveries, unresolvable references and response
  times. Current handling is synchronous and can exceed GoCardless's delivery
  timeout. Durable queued processing remains a release gate.
- Run pretix periodic tasks and monitor their last successful completion.
  Monitor mail queue failures separately from messages being queued.
- Confirmed payments with `manual_review_required` remain paid in pretix.
  Investigate the financial, ticket and accounting consequences manually.
  Later webhooks do not clear the flag. Clear it only through a controlled
  maintenance action after resolving the incident; there is no automatic reset.
- Keep sandbox and live credentials and endpoint secrets separate. Changing
  the selected environment never changes existing references. Live execution
  stays disabled in this build.

## Required external sandbox evidence

Use synthetic customer identity and sandbox bank data only. A local mocked API
test is not external sandbox evidence.

| Scenario | Evidence needed before release |
| --- | --- |
| Guest / new customer | Hosted consent, correct amount, one debit, confirmed pretix order |
| Signed-in repeat buyer | Correct customer/creditor/mandate binding and one independent debit |
| Concurrent checkout / timeout | One remote debit after retry, persisted original idempotency key |
| Aborted / expired flow | Safe continuation or renewal of the same intent, no duplicate debit |
| Early release | Pending payment, usable ticket/download, actual mail receipt, check-in validity |
| Failure before confirmation | Failed payment, plugin-owned ticket validity revoked, notice received |
| Confirmed release | Download and mail only when confirmed under the chosen policy |
| Late chargeback | Persistent warning, one organizer notification, explicit manual resolution |
| Replay / wrong signature / foreign reference | No foreign mutation, replay deduplication, invalid signature rejected |
| Mandate changes | Adverse lifecycle events handled; current implementation still retries them |
| Reminder | Current API check, per-recipient deduplication, no reminder after completion |

Previous external sandbox observations are useful history, not fresh
verification of this wheel. Use synthetic identities for sandbox testing;
do not transfer a real customer's details into the sandbox.

Official references checked on 2026-10-21:

- https://docs.pretix.eu/dev/development/api/payment.html
- https://docs.gocardless.com/docs/api-reference/webhooks
- https://docs.gocardless.com/docs/api-reference/payment

The pretix development documentation currently targets a newer development
version than the tested 2026.8.0 release. Installed source and the explicit
integration test target take precedence for compatibility claims.
