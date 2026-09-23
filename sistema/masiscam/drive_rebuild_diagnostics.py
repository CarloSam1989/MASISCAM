"""Read-only explanations of ownership conflicts; never changes migration decisions."""
from collections import defaultdict
import json

from .drive_rebuild import FOLDER
from .models import Documento, Equipo, RegistroEquipo


def quoted(value):
    # Names from Drive/DB must not inject new lines or terminal control characters.
    return json.dumps(str(value), ensure_ascii=False)


class OwnershipDiagnostics:
    def __init__(self, nodes):
        self.nodes = nodes
        self.equipos = {e.pk: e for e in Equipo.objects.select_related("cliente", "proyecto").order_by("pk")}
        self.references = defaultdict(list)
        for equipo in self.equipos.values():
            if equipo.drive_folder_id:
                self.references[equipo.drive_folder_id].append(("Equipo.drive_folder_id", equipo.pk, equipo.pk))
        for row in RegistroEquipo.objects.exclude(drive_folder_id="").order_by("pk").values_list("pk", "equipo_id", "drive_folder_id"):
            pk, owner, folder = row
            self.references[folder].append(("RegistroEquipo.drive_folder_id", pk, owner))
        for field in ("drive_file_id", "drive_upload_id"):
            for pk, owner, file_id in Documento.objects.exclude(**{field: ""}).order_by("pk").values_list("pk", "equipo_id", field):
                self.references[file_id].append(("Documento." + field, pk, owner))

    def chain(self, id):
        seen, result = set(), []
        while id in self.nodes and id not in seen:
            seen.add(id)
            result.append(id)
            parents = self.nodes[id].get("parents", [])
            id = parents[0] if len(parents) == 1 else None
        return result

    def path(self, id):
        return " / ".join(self.nodes[key]["name"] for key in reversed(self.chain(id)))

    def reference_lines(self, id, equipo_id, indent):
        refs = self.references.get(id, [])
        if not refs:
            return [indent + "Sin referencias directas en BD."]
        lines = []
        for field, pk, owner in refs:
            origin = f"{field} (registro BD #{pk})"
            if owner is None:
                lines.append(f"{indent}{origin}: sin equipo asociado.")
                continue
            equipo = self.equipos[owner]
            role = "equipo actual" if owner == equipo_id else "OTRO EQUIPO"
            lines.append(f"{indent}{origin}: Equipo #{owner} ({role}); cliente={quoted(equipo.razon_social_cliente)}; "
                         f"tipo={equipo.tipo_producto}; serie={quoted(equipo.numero_serie)}")
        return lines

    def lines(self, equipo):
        root = equipo.drive_folder_id
        lines = [f"DIAGNOSTICO SOLO LECTURA - Equipo #{equipo.pk}",
                 f"  Cliente: {quoted(equipo.razon_social_cliente)} (cliente #{equipo.cliente_id})",
                 f"  Tipo: {equipo.tipo_producto}", f"  Serie: {quoted(equipo.numero_serie)}",
                 f"  Carpeta Drive actual: {quoted(self.path(root))}", f"  ID carpeta: {quoted(root)}",
                 "  Referencias de la carpeta actual (no atribuyen sus archivos a un equipo):"]
        lines.extend(self.reference_lines(root, equipo.pk, "    "))
        conflicts = {}
        for id, node in self.nodes.items():
            if node["mimeType"] == FOLDER:
                continue
            chain = self.chain(id)
            if root not in chain:
                continue
            chain = chain[:chain.index(root)]
            effective = next((key for key in chain if key in self.references), None)
            owners = {ref[2] for ref in self.references.get(effective, [])}
            # Explain exactly the existing rule: nearest reference below the shared root.
            if len(owners) == 1 and None not in owners:
                continue
            if not owners:
                reason = "Sin propietario identificado por debajo de la carpeta compartida."
            elif None in owners:
                reason = "Existe una referencia de documento sin equipo asociado."
            else:
                reason = "La referencia mas cercana corresponde a varios equipos."
            conflicts[id] = reason
            for folder in chain[1:]:
                conflicts.setdefault(folder, "Contiene archivos con atribucion conflictiva.")
        lines.append(f"  Archivos/subcarpetas conflictivos: {len(conflicts)}")
        for id in sorted(conflicts, key=lambda key: (self.path(key), key)):
            kind = "Subcarpeta" if self.nodes[id]["mimeType"] == FOLDER else "Archivo"
            lines.extend([f"    {kind}: {quoted(self.path(id))}", f"      ID: {quoted(id)}",
                          f"      Motivo: {conflicts[id]}", "      Referencias directas:"])
            lines.extend(self.reference_lines(id, equipo.pk, "        "))
            ancestors = self.chain(id)[1:]
            if root in ancestors:
                ancestors = ancestors[:ancestors.index(root) + 1]
            referenced = [key for key in ancestors if key in self.references]
            if referenced:
                lines.append("      Referencias por carpetas contenedoras (contexto, no propiedad individual):")
                for parent in referenced:
                    lines.append(f"        Carpeta {quoted(self.path(parent))}; ID={quoted(parent)}")
                    lines.extend(self.reference_lines(parent, equipo.pk, "          "))
        lines.append("  Sin movimientos, creaciones ni cambios en BD. La decision de migracion no cambia.")
        return lines
