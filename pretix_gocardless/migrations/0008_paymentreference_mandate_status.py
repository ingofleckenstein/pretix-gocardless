from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0007_paymentreference_billing_request_flow")]

    operations = [
        migrations.AddField(
            model_name="paymentreference",
            name="mandate_status",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
