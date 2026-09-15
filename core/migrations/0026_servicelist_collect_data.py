from django.db import migrations, models


_COLLECT_FIELDS_TO_CODES = {
    'both': ('user', 'email'),
    'user': ('user',),
    'email': ('email',),
    'serial': ('serial',),
}


def _merge_into_collect_data(apps, schema_editor):
    ServiceList = apps.get_model('core', 'ServiceList')
    for service in ServiceList.objects.all():
        codes = []
        if service.collect_login:
            codes += list(_COLLECT_FIELDS_TO_CODES.get(service.collect_fields, ()))
        for extra in (service.collect_extras or '').split(','):
            extra = extra.strip()
            if extra and extra not in codes:
                codes.append(extra)
        service.collect_data = ','.join(codes)
        service.save(update_fields=['collect_data'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_remove_paymentgateway_vepay_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='servicelist',
            name='collect_data',
            field=models.CharField(blank=True, default='', max_length=300, verbose_name='Dados a solicitar na compra'),
        ),
        migrations.RunPython(_merge_into_collect_data, migrations.RunPython.noop),
    ]