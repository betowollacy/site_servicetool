from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_customer_last_seen'),
    ]

    operations = [
        migrations.CreateModel(
            name='StoreProduct',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255)),
                ('price', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('description', models.TextField(blank=True, null=True)),
                ('thumbnail', models.CharField(blank=True, max_length=500, null=True)),
                ('stock', models.IntegerField(default=0)),
                ('status', models.CharField(default='Active', max_length=20)),
                ('sell_count', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'store_products',
            },
        ),
    ]