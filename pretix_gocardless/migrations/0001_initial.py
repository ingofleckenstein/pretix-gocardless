import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [("pretixbase", "0096_auto_20180722_0801")]

    operations = [
        migrations.CreateModel(
            name="PaymentReference",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("creation_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("environment", models.CharField(choices=[("sandbox", "Sandbox"), ("live", "Live")], max_length=10)),
                ("billing_request_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("remote_payment_id", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("last_status", models.CharField(blank=True, max_length=40)),
                ("reversal_recorded", models.BooleanField(default=False)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("payment", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="gocardless_reference", to="pretixbase.orderpayment")),
            ],
        ),
        migrations.CreateModel(
            name="ProcessedEvent",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("event_id", models.CharField(max_length=64, unique=True)),
                ("processed", models.DateTimeField(auto_now_add=True)),
                ("reference", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="events", to="pretix_gocardless.paymentreference")),
            ],
        ),
    ]
