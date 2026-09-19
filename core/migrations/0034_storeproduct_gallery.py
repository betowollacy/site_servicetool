from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0033_storeproduct'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeproduct',
            name='gallery',
            field=models.JSONField(blank=True, default=list, null=True),
        ),
    ]