from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("pretix_gocardless", "0004_customerlink")]
    operations = [
        migrations.CreateModel(name="ReminderDelivery", fields=[
            ("id", models.AutoField(primary_key=True, serialize=False)),
            ("customer_queued", models.DateTimeField(blank=True, null=True)),
            ("organizer_queued", models.DateTimeField(blank=True, null=True)),
            ("payment", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                    related_name="gocardless_reminder", to="pretixbase.orderpayment")),
        ]),
    ]
