from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0004_enterpriseprojection_siteprojection"),
    ]

    operations = [
        migrations.AlterField(
            model_name="executionorder",
            name="tos_order_id",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.RemoveConstraint(
            model_name="equipmentstate",
            name="unique_equipment_per_terminal",
        ),
        migrations.AddConstraint(
            model_name="executionorder",
            constraint=models.UniqueConstraint(
                fields=("site", "tos_order_id"),
                name="unique_order_per_site",
            ),
        ),
        migrations.AddConstraint(
            model_name="equipmentstate",
            constraint=models.UniqueConstraint(
                fields=("site", "tos_che_id"),
                name="unique_equipment_per_site",
            ),
        ),
        migrations.AlterModelOptions(
            name="equipmentstate",
            options={"ordering": ["site_id", "name"]},
        ),
    ]
