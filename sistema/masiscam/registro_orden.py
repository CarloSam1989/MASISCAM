"""Chronological numbering and resumable Drive moves, retaining every record ID."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from accounts.models import Empresa
from .models import Equipo, Proyecto, RegistroEquipo
from .services import sincronizar_carpeta_equipo

PENDIENTE = "Organización de Drive pendiente. Reintente desde Editar registro."
FOLDER = "application/vnd.google-apps.folder"


@transaction.atomic
def renumerar(equipo_id, tipo, forzar=False):
    Empresa.objects.select_for_update().order_by("pk").first()
    registros = list(RegistroEquipo.objects.select_for_update().filter(
        equipo_id=equipo_id, tipo=tipo).order_by("fecha", "pk"))
    cambio = any(r.secuencia != n for n, r in enumerate(registros, 1))
    if cambio:
        # Release numbers first to satisfy the existing unique constraint on swaps.
        RegistroEquipo.objects.filter(pk__in=[r.pk for r in registros]).update(secuencia=None)
        for numero, registro in enumerate(registros, 1):
            registro.secuencia = numero
        RegistroEquipo.objects.bulk_update(registros, ["secuencia"])
    if cambio or forzar:
        RegistroEquipo.objects.filter(pk__in=[r.pk for r in registros]).update(drive_error=PENDIENTE)
    return registros


def _arbol(servicio, raiz):
    nodos, pendientes, vistos = {}, [raiz], set()
    while pendientes:
        padre = pendientes.pop()
        if padre in vistos or len(vistos) >= 2000:
            raise ValueError("La estructura de carpetas requiere revisión manual.")
        vistos.add(padre)
        for node in servicio.listar_carpetas(padre):
            if node.get("trashed"):
                continue
            if node.get("parents") != [padre] or node["id"] in nodos:
                raise ValueError("La estructura de carpetas es ambigua.")
            nodos[node["id"]] = node
            pendientes.append(node["id"])
    return nodos


def _contenedor(servicio, nodos, padre, nombre, protegidas):
    existentes = [n for n in nodos.values() if n.get("parents") == [padre] and n["name"] == nombre]
    if len(existentes) > 1 or any(n["id"] in protegidas for n in existentes):
        raise ValueError("La carpeta de destino es ambigua o pertenece a otro registro.")
    if existentes:
        return existentes[0]["id"]
    carpeta = servicio._crear_carpeta(nombre, padre)
    nodos[carpeta["id"]] = dict(carpeta, name=nombre, parents=[padre], mimeType=FOLDER)
    return carpeta["id"]


def _organizar(servicio, registros, raiz):
    nodos = _arbol(servicio, raiz)
    protegidas = set(RegistroEquipo.objects.exclude(drive_folder_id="").values_list("drive_folder_id", flat=True))
    protegidas.update(Equipo.objects.exclude(drive_folder_id="").values_list("drive_folder_id", flat=True))
    protegidas.update(Proyecto.objects.exclude(drive_folder_id="").values_list("drive_folder_id", flat=True))
    # Validate all sources before moving any folder, including old direct children.
    for registro in registros:
        if not registro.drive_folder_id:
            propias = [n for n in nodos.values() if n.get("appProperties", {}).get("masiscam_registro") == str(registro.clave_creacion)]
            if len(propias) > 1:
                raise ValueError("El registro tiene varias carpetas; requiere revisión manual.")
            if propias:
                if propias[0]["id"] in protegidas:
                    raise ValueError("La carpeta recuperada pertenece a otro registro o equipo.")
                registro.drive_folder_id = propias[0]["id"]
                registro.drive_folder_url = propias[0].get("webViewLink", "")
                RegistroEquipo.objects.filter(pk=registro.pk).update(
                    drive_folder_id=registro.drive_folder_id, drive_folder_url=registro.drive_folder_url)
                protegidas.add(registro.drive_folder_id)
        if registro.drive_folder_id:
            servicio.validar_carpeta_registro_eliminable(registro)
            if registro.drive_folder_id not in nodos:
                raise ValueError("La carpeta del registro está fuera del equipo.")
    planes = []
    tipo_folder = _contenedor(servicio, nodos, raiz, registros[0].tipo, protegidas)
    for registro in registros:
        destino = _contenedor(servicio, nodos, tipo_folder, str(registro.fecha), protegidas)
        nombre = f"{registro.tipo} {registro.secuencia:03d}"
        temporal = f"REGISTRO-{registro.clave_creacion}"
        if not registro.drive_folder_id:
            carpeta = servicio.drive.files().create(
                body={"name": temporal, "mimeType": FOLDER, "parents": [destino],
                      "appProperties": {"masiscam_registro": str(registro.clave_creacion)}},
                fields="id,webViewLink", supportsAllDrives=True,
            ).execute()
            registro.drive_folder_id = carpeta["id"]
            registro.drive_folder_url = carpeta.get("webViewLink", "")
            RegistroEquipo.objects.filter(pk=registro.pk).update(
                drive_folder_id=registro.drive_folder_id, drive_folder_url=registro.drive_folder_url)
            nodos[carpeta["id"]] = dict(carpeta, name=temporal, parents=[destino], mimeType=FOLDER)
        planes.append((registro, destino, nombre, temporal))
    ids = {r.drive_folder_id for r in registros}
    for registro, destino, nombre, temporal in planes:
        if any(n["id"] not in ids and n.get("parents") == [destino] and n["name"] == nombre for n in nodos.values()):
            raise ValueError("Ya existe una carpeta ajena con el número de destino.")
    # Stage renamed/moved folders before assigning final names, so swaps never
    # temporarily give two records the same name within the same date folder.
    cambios = [(r, dest, name, temp) for r, dest, name, temp in planes
               if nodos[r.drive_folder_id]["name"] != name or nodos[r.drive_folder_id]["parents"] != [dest]]
    for registro, destino, nombre, temporal in cambios:
        if nodos[registro.drive_folder_id]["name"] != temporal:
            servicio.drive.files().update(fileId=registro.drive_folder_id, body={"name": temporal}, supportsAllDrives=True).execute()
    for registro, destino, nombre, temporal in cambios:
        params = {"fileId": registro.drive_folder_id, "body": {"name": nombre}, "supportsAllDrives": True}
        padre = nodos[registro.drive_folder_id]["parents"][0]
        if padre != destino:
            params.update(addParents=destino, removeParents=padre)
        servicio.drive.files().update(**params).execute()


def sincronizar_grupo(equipo_id, tipo):
    """Persist completed IDs on remote failure; a retry resumes the desired layout."""
    error = None
    with transaction.atomic():
        Empresa.objects.select_for_update().order_by("pk").first()
        registros = renumerar(equipo_id, tipo)
        if not registros:
            return
        try:
            if not settings.GOOGLE_DRIVE_ENABLED:
                raise ImproperlyConfigured("Drive no está configurado.")
            raiz = sincronizar_carpeta_equipo(equipo_id)
            if (not raiz or raiz == settings.GOOGLE_DRIVE_ROOT_FOLDER_ID
                    or Equipo.objects.exclude(pk=equipo_id).filter(drive_folder_id=raiz).exists()):
                raise ValueError("La carpeta principal del equipo está compartida o protegida.")
            from .services import GoogleDriveService
            _organizar(GoogleDriveService(), registros, raiz)
        except Exception as exc:
            error = exc
            RegistroEquipo.objects.filter(equipo_id=equipo_id, tipo=tipo).update(drive_error=PENDIENTE)
        else:
            RegistroEquipo.objects.filter(equipo_id=equipo_id, tipo=tipo).update(drive_error="")
    if error:
        raise error
