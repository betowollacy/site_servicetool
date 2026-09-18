from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_inventorydata_max_uses_uses_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='last_seen',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
