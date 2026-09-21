from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("masiscam", "0008_documento_drive_upload_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="rolmasiscam",
            name="cliente",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="acceso_usuario",
                to="masiscam.cliente",
            ),
        ),
        migrations.AlterField(
            model_name="rolmasiscam",
            name="rol",
            field=models.CharField(
                choices=[
                    ("ADMIN", "Administrador MASISCAM"),
                    ("TECNICO", "Técnico"),
                    ("CONSULTA", "Consulta"),
                    ("CLIENTE", "Cliente (solo sus productos)"),
                ],
                default="CONSULTA",
                max_length=12,
            ),
        ),
        migrations.AddConstraint(
            model_name="rolmasiscam",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(rol="CLIENTE", cliente__isnull=False)
                    | (~models.Q(rol="CLIENTE") & models.Q(cliente__isnull=True))
                ),
                name="masiscam_rol_cliente_vinculado",
            ),
        ),
    ]
