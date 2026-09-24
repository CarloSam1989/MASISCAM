"""Plan and apply Drive moves. No copies, uploads or deletions are performed."""
from copy import deepcopy

from django.conf import settings
from .models import Equipo, RegistroEquipo, Documento, Proyecto
from .services import nombre_carpeta_drive, CATEGORIA_CARPETA

FOLDER = "application/vnd.google-apps.folder"
FIELDS = "id,name,mimeType,parents,trashed,webViewLink"


class RebuildError(Exception):
    """Safe operational error, suitable for command output."""


def snapshot(service):
    root = settings.GOOGLE_DRIVE_ROOT_FOLDER_ID
    node = service.drive.files().get(fileId=root, fields=FIELDS, supportsAllDrives=True).execute()
    if node.get("trashed") or node.get("mimeType") != FOLDER:
        raise RebuildError("La raiz MASISCAM no es una carpeta activa.")
    nodes, pending = {root: node}, [root]
    while pending:
        parent = pending.pop()
        escaped = parent.replace("\\", "\\\\").replace("'", "\\'")
        params = {"q": f"'{escaped}' in parents and trashed=false", "fields": f"nextPageToken,files({FIELDS})",
                  "pageSize": 1000, "supportsAllDrives": True, "includeItemsFromAllDrives": True}
        if service.shared_drive_id:
            params.update(corpora="drive", driveId=service.shared_drive_id)
        pages = set()
        while True:
            response = service.drive.files().list(**params).execute()
            for item in response.get("files", []):
                if item.get("trashed"):
                    continue
                if item.get("parents") != [parent] or item["id"] in nodes:
                    raise RebuildError("Arbol Drive ambiguo o ciclico.")
                nodes[item["id"]] = item
                if item["mimeType"] == FOLDER:
                    pending.append(item["id"])
            token = response.get("nextPageToken")
            if not token:
                break
            if token in pages:
                raise RebuildError("Paginacion Drive repetida.")
            pages.add(token)
            params["pageToken"] = token
    return nodes


class Plan:
    def __init__(self, nodes, equipo):
        self.nodes = deepcopy(nodes)
        self.equipo = equipo
        self.operations, self.mapping = [], {}
        self.root = settings.GOOGLE_DRIVE_ROOT_FOLDER_ID
        self.reused = 0

    def children(self, parent):
        return [n for n in self.nodes.values() if n.get("parents") == [parent]]

    def under(self, child, parent):
        seen = set()
        while child in self.nodes and child not in seen:
            if child == parent:
                return True
            seen.add(child)
            parents = self.nodes[child].get("parents", [])
            child = parents[0] if len(parents) == 1 else None
        return False

    def folder(self, name, parent):
        matches = [n for n in self.children(parent) if n["mimeType"] == FOLDER and n["name"] == name]
        if len(matches) > 1:
            raise RebuildError("Hay varias carpetas destino con el mismo nombre; resolver la ambiguedad antes de migrar.")
        if matches:
            self.reused += 1
            return matches[0]["id"]
        id = f"planned-{self.equipo.pk}-{len(self.operations)}"
        self.nodes[id] = {"id": id, "name": name, "mimeType": FOLDER, "parents": [parent]}
        self.operations.append(("create", id, parent, name))
        return id

    def place(self, source, parent, name=None):
        if source not in self.nodes:
            raise RebuildError("Un ID guardado no existe dentro de la raiz MASISCAM.")
        node = self.nodes[source]
        name = name or node["name"]
        if self.under(parent, source):
            raise RebuildError("El movimiento produciria un ciclo.")
        if node["mimeType"] == FOLDER:
            matches = [n for n in self.children(parent) if n["mimeType"] == FOLDER and n["name"] == name and n["id"] != source]
            if len(matches) > 1:
                raise RebuildError("Carpetas destino ambiguas.")
            if matches:
                target = matches[0]["id"]
                for child in list(self.children(source)):
                    self.place(child["id"], target)
                # Keep the empty original skeleton: no deletion and retries can reconstruct mappings.
                self.mapping[source] = target
                self.reused += 1
                return target
        if node.get("parents") != [parent] or node["name"] != name:
            self.operations.append(("move", source, parent, name))
            node.update(parents=[parent], name=name)
        else:
            self.reused += 1
        return source

    def build(self):
        equipo = self.equipo
        old = equipo.drive_folder_id
        if old and (old not in self.nodes or self.nodes[old]["mimeType"] != FOLDER):
            raise RebuildError("Carpeta anterior inexistente o fuera de MASISCAM.")
        records = list(equipo.registros.exclude(drive_folder_id=""))
        documents = list(equipo.documentos.exclude(drive_file_id=""))
        # Uploaded-but-not-confirmed documents may also exist after a lost response.
        for doc in equipo.documentos.filter(drive_file_id="").exclude(drive_upload_id=""):
            if doc.drive_upload_id in self.nodes:
                documents.append(doc)
        relative_paths = {}
        for doc in documents:
            id = doc.drive_file_id or doc.drive_upload_id
            boundary = old if old and self.under(id, old) else equipo.proyecto.drive_folder_id
            if boundary and self.under(id, boundary):
                parents = self.nodes[id].get("parents", [])
                parent = parents[0] if len(parents) == 1 else None
                names = []
                while parent and parent != boundary:
                    names.append(self.nodes[parent]["name"])
                    parents = self.nodes[parent].get("parents", [])
                    parent = parents[0] if len(parents) == 1 else None
                if names:
                    relative_paths[id] = list(reversed(names))
        owners = {}
        for owner, folder in Equipo.objects.exclude(drive_folder_id="").values_list("pk", "drive_folder_id"):
            owners.setdefault(folder, set()).add(owner)
        for owner, folder in RegistroEquipo.objects.exclude(drive_folder_id="").values_list("equipo_id", "drive_folder_id"):
            owners.setdefault(folder, set()).add(owner)
        for owner, id in Documento.objects.exclude(drive_file_id="").values_list("equipo_id", "drive_file_id"):
            owners.setdefault(id, set()).add(owner)
        for owner, id in Documento.objects.exclude(drive_upload_id="").values_list("equipo_id", "drive_upload_id"):
            owners.setdefault(id, set()).add(owner)
        protected = {self.root} | set(Proyecto.objects.exclude(drive_folder_id="").values_list("drive_folder_id", flat=True))
        generic = {"REDUCTOR", "REDUCTORES", "BOMBA", "BOMBAS", nombre_carpeta_drive(equipo.razon_social_cliente).upper()}
        generic.update(nombre_carpeta_drive(name).upper() for name in (
            f"{equipo.cliente.nombre_comercial}-{equipo.cliente.ruc}",
            f"{equipo.razon_social_cliente}-{equipo.cliente.ruc}",
            f"{equipo.proyecto.empresa.nombre} - {equipo.proyecto.empresa_id}",
        ))
        shared = bool(old and (old in protected or owners.get(old) != {equipo.pk}
                              or self.nodes[old]["name"].upper() in generic))
        if old and not shared:
            for id, assigned in owners.items():
                if self.under(id, old) and assigned != {equipo.pk}:
                    raise RebuildError("La carpeta del equipo contiene datos asociados a otro equipo.")
        if shared:
            for node in self.nodes.values():
                if node["mimeType"] == FOLDER or not self.under(node["id"], old):
                    continue
                id, assigned = node["id"], set()
                while id != old:
                    if id in owners:
                        assigned = owners[id]
                        break
                    parents = self.nodes[id].get("parents", [])
                    id = parents[0] if len(parents) == 1 else old
                if len(assigned) != 1 or None in assigned:
                    raise RebuildError("Contenido sin propietario inequívoco en carpeta compartida; no se ha movido.")
        for record in records:
            if record.drive_folder_id not in self.nodes or owners.get(record.drive_folder_id) != {equipo.pk}:
                raise RebuildError("Carpeta de registro compartida o inexistente; revisar atribucion.")
        for doc in documents:
            id = doc.drive_file_id or doc.drive_upload_id
            if id not in self.nodes or owners.get(id) != {equipo.pk}:
                raise RebuildError("Documento compartido, inexistente o fuera de MASISCAM.")
        client = self.folder(nombre_carpeta_drive(equipo.razon_social_cliente), self.root)
        product = self.folder(equipo.tipo_producto, client)
        series = nombre_carpeta_drive(equipo.numero_serie)
        self.target = self.place(old, product, series) if old and not shared else self.folder(series, product)
        # Never reuse another equipment's series folder, even across companies.
        if owners.get(self.target, {equipo.pk}) != {equipo.pk}:
            raise RebuildError("La carpeta destino ya pertenece a otro equipo.")
        for record in records:
            id = self.mapping.get(record.drive_folder_id, record.drive_folder_id)
            if not self.under(id, self.target):
                id = self.place(id, self.target)
            self.mapping[record.drive_folder_id] = id
        for doc in documents:
            id = doc.drive_file_id or doc.drive_upload_id
            if not self.under(id, self.target):
                parent = self.target
                for name in relative_paths.get(id, [CATEGORIA_CARPETA.get(doc.categoria, "08_Otros")]):
                    parent = self.folder(name, parent)
                self.place(id, parent)
        return self

    def execute(self, service, resolved):
        for kind, id, parent, name in self.operations:
            parent, actual = resolved.get(parent, parent), resolved.get(id, id)
            if kind == "create":
                # Re-query before creation so a retry after a lost API response reuses its folder.
                folder = service.obtener_carpeta_equipo(name, parent)
                resolved[id] = folder["id"]
            else:
                node = service.drive.files().get(fileId=actual, fields=FIELDS, supportsAllDrives=True).execute()
                args = {"fileId": actual, "body": {"name": name}, "fields": FIELDS, "supportsAllDrives": True}
                if node.get("parents") != [parent]:
                    args["addParents"] = parent
                    if node.get("parents"):
                        args["removeParents"] = ",".join(node["parents"])
                if node.get("parents") != [parent] or node["name"] != name:
                    service.drive.files().update(**args).execute()
        target = resolved.get(self.target, self.target)
        for old, new in self.mapping.items():
            new = resolved.get(new, new)
            RegistroEquipo.objects.filter(equipo=self.equipo, drive_folder_id=old).update(
                drive_folder_id=new, drive_folder_url=f"https://drive.google.com/drive/folders/{new}", drive_error="")
        Equipo.objects.filter(pk=self.equipo.pk).update(
            drive_folder_id=target, drive_folder_url=f"https://drive.google.com/drive/folders/{target}", drive_error="")
