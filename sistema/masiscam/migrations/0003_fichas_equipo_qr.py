import secrets

from django.db import migrations, models
import masiscam.models


def asignar_tokens_equipos(apps, schema_editor):
    Equipo = apps.get_model("masiscam", "Equipo")
    usados = set(
        Equipo.objects.exclude(token_publico__isnull=True)
        .exclude(token_publico="")
        .values_list("token_publico", flat=True)
    )
    for equipo in Equipo.objects.filter(token_publico__isnull=True).iterator():
        token = secrets.token_urlsafe(32)
        while token in usados:
            token = secrets.token_urlsafe(32)
        Equipo.objects.filter(pk=equipo.pk).update(token_publico=token)
        usados.add(token)


class Migration(migrations.Migration):
    dependencies = [("masiscam", "0002_proyecto_publicar_descripcion_and_more")]

    operations = [
        migrations.AddField(
            model_name="proyecto",
            name="razon_social",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="equipo",
            name="sector",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="equipo",
            name="consulta_publica_activa",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="equipo",
            name="token_publico",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.RunPython(asignar_tokens_equipos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="equipo",
            name="token_publico",
            field=models.CharField(
                default=masiscam.models.generar_token_publico,
                editable=False,
                max_length=64,
                unique=True,
            ),
        ),
    ]
