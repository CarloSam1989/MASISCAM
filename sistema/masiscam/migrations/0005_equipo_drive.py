from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0004_cliente_equipo_producto")]

    operations = [
        migrations.AddField(
            model_name="equipo", name="drive_folder_id",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddField(
            model_name="equipo", name="drive_folder_url",
            field=models.URLField(blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="equipo", name="drive_error",
            field=models.TextField(blank=True, editable=False),
        ),
    ]
