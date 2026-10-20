from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0003_paymentreference_released_pending_tickets"),
                    ("pretixbase", "__first__")]
    operations = [
        migrations.CreateModel(
            name="CustomerLink",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("environment", models.CharField(max_length=10, choices=[("sandbox", "Sandbox"), ("live", "Live")])),
                ("creditor_id", models.CharField(max_length=64)),
                ("remote_customer_id", models.CharField(max_length=64)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                                related_name="gocardless_links", to="pretixbase.customer")),
            ],
            options={"unique_together": {("customer", "environment", "creditor_id"),
                                         ("environment", "creditor_id", "remote_customer_id")}},
        ),
    ]

