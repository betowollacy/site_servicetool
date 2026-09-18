from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_api_panel_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventorydata',
            name='max_uses',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='inventorydata',
            name='uses_count',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
