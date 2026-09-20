import json
from collections import Counter
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.core.exceptions import ValidationError
from django.db import transaction, connection
from django.apps import apps
from django.utils import timezone


class Command(BaseCommand):
    help = "Validar una exportación histórica selectiva; importa solo con --apply sobre destino vacío."
    labels = ["auth.user", "accounts.empresa", "accounts.perfil", "masiscam.rolmasiscam", "masiscam.proyecto", "masiscam.cliente", "masiscam.equipo", "masiscam.registroequipo", "masiscam.documento", "masiscam.auditoria"]

    def add_arguments(self, parser):
        parser.add_argument("archivo")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--apply", action="store_true")
        group.add_argument("--dry-run", action="store_true", help="Validar sin escribir en DB (predeterminado).")
        parser.add_argument("--allow-superusers", action="store_true", help="Conservar flags staff/superuser solo tras revisión explícita.")
        parser.add_argument("--empresa-id", action="append", type=int, help="Lista aprobada; rechaza empresas fuera de ella, no filtra a ciegas.")

    def handle(self, *args, **options):
        try:
            entries = json.loads(Path(options["archivo"]).read_text(encoding="utf-8-sig"))
            converted = self.validate(entries, options)
        except CommandError:
            raise
        except (OSError, ValueError, TypeError, KeyError, ValidationError):
            raise CommandError("Exportación inválida. Revise estructura, tipos y relaciones; no se import? información.") from None
        counts = Counter(x["model"] for x in converted)
        for label in self.labels:
            self.stdout.write(f"{label}: {counts[label]}")
        elevated = sum(bool(x["fields"].get("is_superuser") or x["fields"].get("is_staff")) for x in entries if x.get("model") in ("core.usuario", "auth.user"))
        self.stdout.write(f"Identidades con flags elevados en origen: {elevated}; conservar: {bool(options['allow_superusers'])}")
        if not options["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Validados sin escribir: {len(converted)} objetos. Media se copia por separado."))
            return
        models = set()
        try:
            with transaction.atomic():
                for item in serializers.deserialize("json", json.dumps(converted)):
                    if type(item.object).objects.filter(pk=item.object.pk).exists():
                        raise CommandError("PK destino existente: importación cancelada sin sobrescribir.")
                    item.save()
                    models.add(type(item.object))
                connection.check_constraints()
                with connection.cursor() as cursor:
                    for sql in connection.ops.sequence_reset_sql(no_style(), list(models)):
                        cursor.execute(sql)
        except CommandError:
            raise
        except Exception:
            raise CommandError("Importación cancelada. Revise restricciones del destino; no se sobrescribieron datos.") from None
        self.stdout.write(self.style.SUCCESS(f"Importados: {len(converted)} objetos. Verifique media y tokens."))

    def validate(self, entries, options):
        if not isinstance(entries, list) or not entries:
            raise CommandError("Se requiere una lista no vacía de objetos selectivos.")
        aliases = {"core.usuario": "auth.user", "core.empresa": "accounts.empresa", "core.perfil": "accounts.perfil"}
        converted, index = [], {}
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("fields"), dict):
                raise CommandError("Objeto de exportación inválido.")
            label = aliases.get(entry.get("model"), entry.get("model"))
            if label not in self.labels:
                raise CommandError("La exportación incluye modelos ajenos a MASISCAM.")
            pk = entry.get("pk")
            if type(pk) is not int or pk <= 0 or (label, pk) in index:
                raise CommandError("PK inválida o repetida en exportación.")
            model = apps.get_model(label)
            fields = dict(entry["fields"])
            if label == "auth.user":
                names = {f.name for f in get_user_model()._meta.fields} - {"id"}
                fields = {k: v for k, v in fields.items() if k in names}
                if entry["fields"].get("activo") is False:
                    fields["is_active"] = False
                if not options["allow_superusers"]:
                    fields["is_staff"] = fields["is_superuser"] = False
            elif label == "accounts.empresa":
                fields = {k: v for k, v in fields.items() if k in ("nombre", "direccion", "activa")}
                if options["empresa_id"] and pk not in options["empresa_id"]:
                    raise CommandError("Empresa fuera de la lista aprobada.")
            elif label == "accounts.perfil":
                fields = {k: v for k, v in fields.items() if k in ("user", "empresa", "activo")}
            names = {f.name for f in model._meta.fields} - {"id"}
            if set(fields) - names:
                raise CommandError("Modelo MASISCAM con campos desconocidos; revisar versión fuente.")
            if model.objects.filter(pk=pk).exists():
                raise CommandError("PK destino existente; no se permite sobrescritura.")
            for field in model._meta.fields:
                if field.name not in fields:
                    if getattr(field, "auto_now", False) or getattr(field, "auto_now_add", False):
                        fields[field.name] = timezone.now().isoformat()
                    if not field.primary_key and not field.has_default() and not field.blank and not field.null and not getattr(field, "auto_now", False) and not getattr(field, "auto_now_add", False):
                        raise CommandError("Exportación incompleta: campo requerido ausente.")
                    continue
                value = fields[field.name]
                if field.is_relation:
                    if value is not None and (type(value) is not int or value <= 0):
                        raise CommandError("Relación inválida.")
                else:
                    value = field.to_python(value)
                    if value is None and not field.null:
                        raise CommandError("Campo no admite nulo.")
                    if field.get_internal_type() in ("FileField", "ImageField") and value:
                        name = str(value).replace("\\", "/")
                        if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or ":" in name:
                            raise CommandError("Ruta de archivo insegura.")
                        field.run_validators(SimpleNamespace(name=name))
                    elif value not in (None, ""):
                        field.run_validators(value)
            item = {"model": label, "pk": pk, "fields": fields}
            converted.append(item); index[(label, pk)] = fields
        for item in converted:
            model = apps.get_model(item["model"])
            for field in model._meta.fields:
                if not field.is_relation:
                    continue
                value = item["fields"].get(field.name)
                if value is not None and (field.related_model._meta.label_lower, value) not in index:
                    raise CommandError("Falta una dependencia FK en la exportación selectiva.")
            f = item["fields"]
            if item["model"] == "masiscam.equipo" and f.get("cliente"):
                if index[("masiscam.cliente", f["cliente"])]["empresa"] != index[("masiscam.proyecto", f["proyecto"])]["empresa"]:
                    raise CommandError("Equipo y cliente pertenecen a empresas diferentes.")
            if item["model"] == "masiscam.documento" and f.get("equipo"):
                if index[("masiscam.equipo", f["equipo"])]["proyecto"] != f["proyecto"]:
                    raise CommandError("Documento y equipo pertenecen a proyectos diferentes.")
            if item["model"] == "masiscam.auditoria" and f.get("proyecto"):
                if index[("masiscam.proyecto", f["proyecto"])]["empresa"] != f["empresa"]:
                    raise CommandError("Auditoría y proyecto pertenecen a empresas diferentes.")
        return sorted(converted, key=lambda x: self.labels.index(x["model"]))
