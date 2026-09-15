from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_customer_session_token'),
    ]

    operations = [
        migrations.AddField(
            model_name='paymentdeposit',
            name='gateway_fee',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'),
                                      help_text='Taxa cobrada pelo gateway sobre o depósito.',
                                      max_digits=12),
        ),
        migrations.AddField(
            model_name='paymentdeposit',
            name='net_amount',
            field=models.DecimalField(decimal_places=2, max_digits=12, null=True, blank=True,
                                      help_text='Valor líquido recebido após as taxas do gateway (ex.: netValue do Asaas).'),
        ),
    ]