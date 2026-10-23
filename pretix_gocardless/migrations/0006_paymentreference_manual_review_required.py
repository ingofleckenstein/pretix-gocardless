from django.db import migrations, models


def preserve_existing_incidents(apps, schema_editor):
    Reference = apps.get_model("pretix_gocardless", "PaymentReference")
    Reference.objects.using(schema_editor.connection.alias).filter(
        payment__state="confirmed",
        last_status__in=["failed", "cancelled", "customer_approval_denied", "charged_back"],
    ).update(manual_review_required=True)


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0005_reminderdelivery")]
    operations = [
        migrations.AddField(
            model_name="paymentreference", name="manual_review_required",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(preserve_existing_incidents, migrations.RunPython.noop),
    ]
