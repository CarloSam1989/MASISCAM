import json
import tempfile
from pathlib import Path
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.test import TestCase
from accounts.models import Empresa, Perfil
from masiscam.models import Equipo, RegistroEquipo
from django.urls import reverse


class ImportacionTests(TestCase):
    def test_validar_importar_y_evitar_sobrescritura(self):
        datos = [
            {"model": "core.usuario", "pk": 17, "fields": {"username": "USUARIO.MIGRADO", "password": "!", "is_active": True, "activo": True, "is_staff": False, "is_superuser": False, "sistema_preferido": "SYSCloud"}},
            {"model": "core.empresa", "pk": 9, "fields": {"nombre": "Empresa migrada", "direccion": "D", "activa": True, "estado_suscripcion": "ACTIVA"}},
            {"model": "core.perfil", "pk": 31, "fields": {"user": 17, "empresa": 9, "activo": True, "rol": "GERENTE", "sistemas": [99]}},
            {"model": "masiscam.rolmasiscam", "pk": 45, "fields": {"perfil": 31, "rol": "ADMIN", "activo": True}},
            {"model": "masiscam.proyecto", "pk": 61, "fields": {"empresa": 9, "codigo": "CAM-ORIGINAL", "nombre": "LOS TIGRES", "razon_social": "Empresa migrada", "cliente": "Cliente", "responsable": "R", "fecha_inicio": "2026-09-16", "creado_por": 17, "token_publico": "TOKEN-PROYECTO-ORIGINAL", "creado_en": "2026-09-16T12:00:00Z", "actualizado_en": "2026-09-16T12:00:00Z"}},
            {"model": "masiscam.cliente", "pk": 71, "fields": {"empresa": 9, "nombre_comercial": "Cliente", "razon_social": "Empresa migrada", "ruc": "0991234567001"}},
            {"model": "masiscam.equipo", "pk": 81, "fields": {"proyecto": 61, "cliente": 71, "nombre": "RED-ORIGINAL", "ubicacion": "E", "sector": "S", "marca": "M", "modelo": "X", "numero_serie": "SER-ORIGINAL", "potencia_hp": "20 HP", "ratio": "15:1", "tipo_aceite": "ISO VG 220", "consulta_publica_activa": True, "token_publico": "TOKEN-QR-ORIGINAL", "drive_folder_id": "drive-equipo-original", "drive_folder_url": "https://drive.google.com/drive/folders/drive-equipo-original", "creado_en": "2026-09-16T12:00:00Z", "actualizado_en": "2026-09-16T12:00:00Z"}},
            {"model": "masiscam.registroequipo", "pk": 91, "fields": {"equipo": 81, "tipo": "NUEVO", "fecha": "2026-09-16", "clave_creacion": "00000000-0000-0000-0000-000000000091", "drive_folder_id": "drive-registro-original", "creado_en": "2026-09-16T12:00:00Z"}},
        ]
        with tempfile.TemporaryDirectory() as directorio:
            archivo = Path(directorio) / "datos.json"
            archivo.write_text(json.dumps(datos), encoding="utf-8")
            call_command("importar_syscloud", str(archivo), stdout=StringIO())
            self.assertFalse(Empresa.objects.exists())
            self.assertFalse(get_user_model().objects.exists())
            call_command("importar_syscloud", str(archivo), apply=True, stdout=StringIO())
            self.assertEqual(Empresa.objects.get(pk=9).nombre, "Empresa migrada")
            self.assertEqual(Perfil.objects.get(pk=31).rol_masiscam.rol, "ADMIN")
            self.assertEqual(get_user_model().objects.get(pk=17).username, "USUARIO.MIGRADO")
            equipo = Equipo.objects.get(pk=81)
            self.assertEqual(equipo.token_publico, "TOKEN-QR-ORIGINAL")
            self.assertEqual(equipo.drive_folder_id, "drive-equipo-original")
            self.assertEqual(equipo.ratio, "15:1")
            self.assertEqual(equipo.tipo_aceite, "ISO VG 220")
            self.assertEqual(RegistroEquipo.objects.get(pk=91).drive_folder_id, "drive-registro-original")
            publico = self.client.get(reverse("masiscam:equipo_publico", args=[equipo.token_publico]))
            self.assertContains(publico, "15:1")
            with self.assertRaises(CommandError):
                call_command("importar_syscloud", str(archivo), apply=True, stdout=StringIO())
            self.assertEqual(Empresa.objects.count(), 1)
