from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from masiscam.services import GoogleDriveService


class Command(BaseCommand):
    help = "Diagnóstico de solo lectura: credencial y acceso a carpeta raíz Drive."

    def handle(self, *args, **options):
        if not settings.GOOGLE_DRIVE_ENABLED:
            raise CommandError("Google Drive est? deshabilitado.")
        if not settings.GOOGLE_DRIVE_CREDENTIALS_FILE or not Path(settings.GOOGLE_DRIVE_CREDENTIALS_FILE).is_file():
            raise CommandError("Credencial Drive ausente o no accesible.")
        try:
            service = GoogleDriveService()
            root = service.drive.files().get(fileId=settings.GOOGLE_DRIVE_ROOT_FOLDER_ID,
                fields="mimeType,trashed,capabilities(canAddChildren),driveId", supportsAllDrives=True).execute()
            if root.get("trashed") or root.get("mimeType") != "application/vnd.google-apps.folder":
                raise ValueError()
            if not root.get("capabilities", {}).get("canAddChildren"):
                raise ValueError()
            if settings.GOOGLE_DRIVE_SHARED_DRIVE_ID and root.get("driveId") != settings.GOOGLE_DRIVE_SHARED_DRIVE_ID:
                raise ValueError()
        except Exception:
            raise CommandError("No se pudo verificar acceso y permisos de la raíz Drive. Revise la configuración privada.") from None
        self.stdout.write(self.style.SUCCESS("Drive: credencial válida y raíz accesible con permiso de carga. No se modificaron archivos."))
