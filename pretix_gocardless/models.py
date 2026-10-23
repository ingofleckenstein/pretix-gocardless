"""Local links between pretix orders, GoCardless resources and event history."""
import uuid

from django.db import models


class CustomerLink(models.Model):
    """Reusable GoCardless customer identity for one pretix customer/account."""
    id = models.AutoField(primary_key=True)
    customer = models.ForeignKey("pretixbase.Customer", on_delete=models.CASCADE, related_name="gocardless_links")
    environment = models.CharField(max_length=10, choices=(("sandbox", "Sandbox"), ("live", "Live")))
    creditor_id = models.CharField(max_length=64)
    remote_customer_id = models.CharField(max_length=64)

    class Meta:
        unique_together = (("customer", "environment", "creditor_id"),
                           ("environment", "creditor_id", "remote_customer_id"))


class PaymentReference(models.Model):
    """One pretix payment intent and every remote resource created for it."""
    id = models.AutoField(primary_key=True)
    payment = models.OneToOneField("pretixbase.OrderPayment", on_delete=models.PROTECT, related_name="gocardless_reference")
    # Random keys are saved before remote calls so retries can reuse them.
    creation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    environment = models.CharField(max_length=10, choices=(("sandbox", "Sandbox"), ("live", "Live")))
    creditor_id = models.CharField(max_length=64, null=True, blank=True)
    billing_request_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    # Persist only the flow identifier. The authorisation URL contains an
    # access token and must never be stored in the database.
    billing_request_flow_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    # An expired flow gets a new generation, hence a new idempotency key;
    # retries within the same generation still converge on one flow.
    billing_request_flow_generation = models.PositiveIntegerField(default=0)
    mandate_id = models.CharField(max_length=64, null=True, blank=True)
    # Kept separately from last_status, which always describes the payment.
    mandate_status = models.CharField(max_length=32, blank=True, default="")
    remote_payment_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    last_status = models.CharField(max_length=40, blank=True)
    reversal_recorded = models.BooleanField(default=False)
    # A review flag is not an accounting reversal. Keep it until an operator
    # explicitly resolves the incident, independently of later API snapshots.
    manual_review_required = models.BooleanField(default=False)
    released_pending_tickets = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)


class ProcessedEvent(models.Model):
    id = models.AutoField(primary_key=True)
    # GoCardless event IDs, not webhook delivery IDs, are the deduplication key.
    event_id = models.CharField(max_length=64, unique=True)
    reference = models.ForeignKey(PaymentReference, on_delete=models.PROTECT, related_name="events")
    processed = models.DateTimeField(auto_now_add=True)


class ReminderDelivery(models.Model):
    """Track each recipient independently; queued mail and markers commit together."""
    id = models.AutoField(primary_key=True)
    payment = models.OneToOneField("pretixbase.OrderPayment", on_delete=models.CASCADE,
                                   related_name="gocardless_reminder")
    customer_queued = models.DateTimeField(null=True, blank=True)
    organizer_queued = models.DateTimeField(null=True, blank=True)
