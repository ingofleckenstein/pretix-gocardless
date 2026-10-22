from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0006_paymentreference_manual_review_required")]
    operations = [
        migrations.AddField(
            model_name="paymentreference", name="billing_request_flow_id",
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="paymentreference", name="billing_request_flow_generation",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
