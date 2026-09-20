from getpass import getpass
import os
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from accounts.models import Empresa, Perfil
from masiscam.models import RolMasiscam


class Command(BaseCommand):
    help = "Crear organizacion y administrador local; ejecutar solo sobre la base propia MASISCAM."

    def add_arguments(self, parser):
        parser.add_argument("--empresa", required=True)
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", default="")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(username=options["username"]).exists():
            raise CommandError("El usuario ya existe; configure su membresia en /admin/.")
        password = os.environ.get("MASISCAM_INITIAL_PASSWORD") or getpass("Clave del administrador MASISCAM: ")
        try:
            validate_password(password)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        empresas = Empresa.objects.filter(nombre=options["empresa"])
        if empresas.count() > 1:
            raise CommandError("Hay varias organizaciones con ese nombre; elija otra o use /admin/.")
        empresa = empresas.first() or Empresa.objects.create(nombre=options["empresa"])
        user = User.objects.create_superuser(username=options["username"], email=options["email"], password=password)
        perfil = Perfil.objects.create(user=user, empresa=empresa)
        RolMasiscam.objects.create(perfil=perfil, rol=RolMasiscam.Rol.ADMINISTRADOR)
        self.stdout.write(self.style.SUCCESS("Administrador y organizacion MASISCAM creados."))
