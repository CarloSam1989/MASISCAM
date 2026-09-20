from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0003_fichas_equipo_qr")]

    operations = [
        migrations.CreateModel(
            name="Cliente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre_comercial", models.CharField(max_length=200, verbose_name="Cliente / nombre comercial")),
                ("ruc", models.CharField(max_length=20, verbose_name="RUC")),
                ("razon_social", models.CharField(max_length=200, verbose_name="Razón social")),
                ("correo", models.EmailField(blank=True, max_length=254, verbose_name="Correo")),
                ("telefono", models.CharField(blank=True, max_length=40, verbose_name="Teléfono")),
                ("empresa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="clientes_masiscam", to="accounts.empresa")),
            ],
            options={
                "ordering": ["nombre_comercial", "pk"],
                "constraints": [models.UniqueConstraint(fields=("empresa", "ruc"), name="masiscam_cliente_empresa_ruc_uniq")],
            },
        ),
        migrations.AddField(
            model_name="equipo",
            name="tipo_producto",
            field=models.CharField(choices=[("REDUCTOR", "Reductores")], default="REDUCTOR", editable=False, max_length=32),
        ),
        migrations.AddField(
            model_name="equipo",
            name="cliente",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="equipos", to="masiscam.cliente"),
        ),
    ]
