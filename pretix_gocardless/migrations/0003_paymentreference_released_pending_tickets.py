from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0002_paymentreference_mandate_creditor")]

    operations = [
        migrations.AddField(model_name="paymentreference", name="released_pending_tickets",
                            field=models.BooleanField(default=False)),
    ]
