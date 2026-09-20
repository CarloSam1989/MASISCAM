import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0005_equipo_drive")]

    operations = [
        migrations.CreateModel(
            name="RegistroEquipo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("NUEVO", "Nuevo"), ("ASISTENCIA", "Asistencia"), ("GARANTIA", "Garantía")], max_length=12)),
                ("fecha", models.DateField()),
                ("observacion", models.TextField(blank=True)),
                ("clave_creacion", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("drive_folder_id", models.CharField(blank=True, editable=False, max_length=255)),
                ("drive_folder_url", models.URLField(blank=True, editable=False)),
                ("drive_error", models.TextField(blank=True, editable=False)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("equipo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="registros", to="masiscam.equipo")),
            ],
            options={"ordering": ["-fecha", "-creado_en", "-pk"], "constraints": [models.UniqueConstraint(fields=("equipo", "clave_creacion"), name="masiscam_registro_envio_uniq")]},
        ),
    ]
