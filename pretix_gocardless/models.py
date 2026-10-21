"""Persistent references for future transactional webhook handling."""
import uuid

from django.db import models


class CustomerLink(models.Model):
    id = models.AutoField(primary_key=True)
    customer = models.ForeignKey("pretixbase.Customer", on_delete=models.CASCADE, related_name="gocardless_links")
    environment = models.CharField(max_length=10, choices=(("sandbox", "Sandbox"), ("live", "Live")))
    creditor_id = models.CharField(max_length=64)
    remote_customer_id = models.CharField(max_length=64)

    class Meta:
        unique_together = (("customer", "environment", "creditor_id"),
                           ("environment", "creditor_id", "remote_customer_id"))


class PaymentReference(models.Model):
    id = models.AutoField(primary_key=True)
    payment = models.OneToOneField("pretixbase.OrderPayment", on_delete=models.PROTECT, related_name="gocardless_reference")
    # Random keys are saved before remote calls so retries can reuse them.
    creation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    environment = models.CharField(max_length=10, choices=(("sandbox", "Sandbox"), ("live", "Live")))
    creditor_id = models.CharField(max_length=64, null=True, blank=True)
    billing_request_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    mandate_id = models.CharField(max_length=64, null=True, blank=True)
    remote_payment_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    # The flow URL is intentionally not persisted: it contains an access token.
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
