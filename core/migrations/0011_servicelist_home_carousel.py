from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_invoice_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicelist',
            name='home_carousel',
            field=models.BooleanField(default=False),
        ),
    ]
