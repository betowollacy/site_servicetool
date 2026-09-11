from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_servicelist_carousel'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicelist',
            name='api_enabled',
            field=models.BooleanField(default=True, verbose_name='API ativa'),
        ),
    ]
