from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_servicelist_home_carousel'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicelist',
            name='carousel',
            field=models.CharField(blank=True, choices=[('promocoes', 'Promoções do Dia'), ('desbloqueios', 'Métodos de Desbloqueio')], default='', max_length=20),
        ),
        migrations.RemoveField(
            model_name='servicelist',
            name='home_carousel',
        ),
    ]
