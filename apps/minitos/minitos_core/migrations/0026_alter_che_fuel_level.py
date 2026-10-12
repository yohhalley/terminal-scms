from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0025_remove_equipmenttype_fuel_consumption_rate_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='che',
            name='fuel_level',
            field=models.FloatField(default=100.0),
        ),
    ]
