from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="paymentreference", name="creditor_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="paymentreference", name="mandate_id",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
