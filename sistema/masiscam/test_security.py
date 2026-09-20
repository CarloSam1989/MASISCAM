import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import TestCase, override_settings
from django.urls import reverse
from httplib2 import Response
from googleapiclient.errors import HttpError
from accounts.models import Empresa, Perfil
from .models import Proyecto, Equipo, Documento, RolMasiscam
from .services import GoogleDriveService
from .tasks import sincronizar_documento, DriveSyncError


class SecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="test-reader", password="test-only-password")
        cls.empresa = Empresa.objects.create(nombre="Test organization")
        cls.otra = Empresa.objects.create(nombre="Other organization")
        perfil = Perfil.objects.create(user=cls.user, empresa=cls.empresa)
        cls.rol = RolMasiscam.objects.create(perfil=perfil, rol="CONSULTA")
        cls.proyecto = Proyecto.objects.create(empresa=cls.empresa, codigo="TEST", nombre="Test project", cliente="Test", responsable="Test", fecha_inicio=date.today(), creado_por=cls.user, pagina_publica_activa=True)
        cls.proyecto_otro = Proyecto.objects.create(empresa=cls.otra, codigo="OTHER", nombre="Other project", cliente="Test", responsable="Test", fecha_inicio=date.today(), creado_por=cls.user, pagina_publica_activa=True)
        with patch("masiscam.signals.encolar_carpeta_equipo"):
            cls.equipo = Equipo.objects.create(proyecto=cls.proyecto, nombre="EQ", consulta_publica_activa=True, visible_publico=True)
            cls.ajeno = Equipo.objects.create(proyecto=cls.proyecto_otro, nombre="OTHER-EQ")
        cls.documento = Documento.objects.create(proyecto=cls.proyecto, archivo="doc.pdf", titulo="Test PDF", subido_por=cls.user, nombre_original="test.pdf", tipo_mime="application/pdf", publico=True, estado_sincronizacion="SINCRONIZADO", hash_sha256="a"*64)
        cls.doc_ajeno = Documento.objects.create(proyecto=cls.proyecto_otro, archivo="other.pdf", titulo="Private PDF", subido_por=cls.user, hash_sha256="b"*64)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.temp.name)
        self.override.enable(); self.addCleanup(self.override.disable)
        Path(self.temp.name, "doc.pdf").write_bytes(b"%PDF-1.7 test")
        Path(self.temp.name, "picture.png").write_bytes(b"test-picture")
        Proyecto.objects.filter(pk=self.proyecto.pk).update(imagen_principal="picture.png")
        Equipo.objects.filter(pk=self.equipo.pk).update(fotografia_general="picture.png", fotografia_placa="picture.png")

    def login(self):
        self.client.force_login(self.user)

    def private_urls(self):
        return [reverse("masiscam:proyecto_imagen_privada",args=[self.proyecto.pk]), reverse("masiscam:equipo_foto_privada",args=[self.equipo.pk,"placa"]), reverse("masiscam:documento_privado",args=[self.proyecto.pk,self.documento.pk])]

    def test_private_media_login_role_and_no_direct_media(self):
        for url in self.private_urls(): self.assertEqual(self.client.get(url).status_code,302)
        self.login()
        for url in self.private_urls():
            response=self.client.get(url); self.assertEqual(response.status_code,200)
            self.assertEqual(response["Cache-Control"],"private, no-store"); response.close()
        self.assertEqual(self.client.get("/media/doc.pdf").status_code,404)
        self.rol.activo=False;self.rol.save()
        for url in self.private_urls():self.assertIn(self.client.get(url).status_code,(302,403))

    def test_cross_company_idor_and_unknown_field(self):
        self.login()
        for url in [reverse("masiscam:proyecto_imagen_privada",args=[self.proyecto_otro.pk]),reverse("masiscam:equipo_foto_privada",args=[self.ajeno.pk,"placa"]),reverse("masiscam:documento_privado",args=[self.proyecto.pk,self.doc_ajeno.pk]),reverse("masiscam:equipo_foto_privada",args=[self.equipo.pk,"invalid"])]:
            self.assertEqual(self.client.get(url).status_code,404)

    def test_file_missing_traversal_and_download_name(self):
        self.login();Proyecto.objects.filter(pk=self.proyecto.pk).update(imagen_principal="../private.txt")
        self.assertEqual(self.client.get(self.private_urls()[0]).status_code,404)
        Documento.objects.filter(pk=self.documento.pk).update(archivo="missing.pdf")
        response=self.client.get(self.private_urls()[2]); self.assertEqual(response.status_code,404)
        self.assertNotContains(response,self.temp.name,status_code=404)
        Documento.objects.filter(pk=self.documento.pk).update(archivo="doc.pdf",nombre_original='bad"name.pdf')
        response=self.client.get(self.private_urls()[2]);self.assertTrue(response["Content-Disposition"].startswith("attachment;"));response.close()

    def test_public_media_visibility_and_inactive_company(self):
        url=reverse("masiscam:imagen_proyecto_publica",args=[self.proyecto.token_publico,"proyecto",0])
        response=self.client.get(url);self.assertEqual(response.status_code,200);response.close()
        eq=reverse("masiscam:equipo_foto_publica",args=[self.equipo.token_publico,"placa"])
        response=self.client.get(eq);self.assertEqual(response.status_code,200);response.close()
        doc=reverse("masiscam:documento_publico",args=[self.proyecto.token_publico,self.documento.pk])
        response=self.client.get(doc);self.assertEqual(response.status_code,200);response.close()
        self.empresa.activa=False;self.empresa.save()
        for target in [url,eq,doc]:self.assertEqual(self.client.get(target).status_code,404)
        self.assertNotContains(self.client.get(reverse("masiscam:equipo_publico",args=[self.equipo.token_publico])),"test-picture")

    def test_templates_legal_login_and_protected_links(self):
        login=self.client.get(reverse("accounts:login"))
        for text in ['Bienvenido','autocomplete="username"','autocomplete="current-password"','/terminos/','/privacidad/']:
            self.assertContains(login,text)
        self.assertNotContains(login,"Crear cuenta")
        self.assertContains(self.client.post(reverse("accounts:login"),{"username":"unknown","password":"wrong"}), 'role="alert"')
        for url in ['/terminos/','/privacidad/']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.login()
        response=self.client.get(reverse("masiscam:proyecto_detalle",args=[self.proyecto.pk]))
        self.assertContains(response,self.private_urls()[0]);self.assertContains(response,self.private_urls()[2]);self.assertNotContains(response,"/media/")
        response=self.client.get(reverse("masiscam:publico",args=[self.proyecto.token_publico]))
        self.assertNotContains(response,"/media/")
        self.assertEqual(self.client.get(reverse("masiscam:proyectos")).status_code,200)

    def test_upload_extension_mime_and_office_structure(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .forms import DocumentoForm
        data={"categoria":"INFORMES","titulo":"Test document","descripcion":"","publico":False}
        for name,body,mime in [('fake.png',b'%PDF-1.7','application/pdf'),('fake.pdf',b'<html>unsafe</html>','application/pdf'),('fake.docx',b'not-a-zip','application/vnd.openxmlformats-officedocument.wordprocessingml.document')]:
            form=DocumentoForm(data,{'archivo':SimpleUploadedFile(name,body,content_type=mime)},proyecto=self.proyecto)
            self.assertFalse(form.is_valid());self.assertIn('archivo',form.errors)
        form=DocumentoForm(data,{'archivo':SimpleUploadedFile('valid.pdf',b'%PDF-1.7 test',content_type='application/pdf')},proyecto=self.proyecto)
        self.assertTrue(form.is_valid(),form.errors)

    def test_photo_validator_rejects_format_and_dimensions(self):
        from django.core.exceptions import ValidationError
        from .forms import validar_fotografia
        file=MagicMock();file.name='photo.png';file.size=100;file.image.format='PNG';file.image.width=500;file.image.height=500
        validar_fotografia(file)
        file.name='photo.svg'
        with self.assertRaises(ValidationError):validar_fotografia(file)
        file.name='photo.png';file.image.width=10000;file.image.height=10000
        with self.assertRaises(ValidationError):validar_fotografia(file)

    def test_health_safe_success_and_failure(self):
        response=self.client.get('/health/');self.assertEqual(response.json(),{"status":"ok"})
        with patch('config.health.connection.cursor',side_effect=DatabaseError('secret private path')):
            response=self.client.get('/health/')
        self.assertEqual(response.status_code,503);self.assertEqual(response.json(),{"status":"unavailable"})
        self.assertNotContains(response,'secret',status_code=503)

    @override_settings(GOOGLE_DRIVE_ENABLED=False)
    def test_disabled_drive_no_api(self):
        with patch('masiscam.tasks.GoogleDriveService',create=True) as api:
            from .services import encolar_drive
            self.assertFalse(encolar_drive(sincronizar_documento,self.documento.pk))
            self.assertEqual(sincronizar_documento(self.documento.pk),'')
            api.assert_not_called()

    def test_drive_readonly_diagnostic(self):
        with tempfile.NamedTemporaryFile() as credential,override_settings(GOOGLE_DRIVE_CREDENTIALS_FILE=credential.name,GOOGLE_DRIVE_ENABLED=True),patch('masiscam.management.commands.verificar_drive.GoogleDriveService') as service:
            service.return_value.drive.files().get.return_value.execute.return_value={'mimeType':'application/vnd.google-apps.folder','capabilities':{'canAddChildren':True}}
            call_command('verificar_drive',stdout=io.StringIO())
            service.return_value.drive.files().create.assert_not_called()
            service.return_value.drive.files().update.assert_not_called()
            service.return_value.drive.files().delete.assert_not_called()

    def test_document_retry_after_remote_success_db_failure_no_duplicate(self):
        Documento.objects.filter(pk=self.documento.pk).update(estado_sincronizacion='PENDIENTE')
        Proyecto.objects.filter(pk=self.proyecto.pk).update(drive_folder_id='folder-test')
        remote={};api=MagicMock();api.files().generateIds.return_value.execute.return_value={'ids':['stable-upload-id']}
        def get(fileId,**kwargs):
            def execute():
                if fileId not in remote:raise HttpError(Response({'status':'404'}),b'not found')
                return remote[fileId]
            return MagicMock(execute=execute)
        def create(body,**kwargs):
            def execute():
                remote[body['id']]={'id':body['id'],'webViewLink':'https://drive.google.com/test'}
                return remote[body['id']]
            return MagicMock(execute=execute)
        api.files().get.side_effect=get;api.files().create.side_effect=create
        service=object.__new__(GoogleDriveService);service.drive=api;service.shared_drive_id=''
        service.buscar_subcarpeta=MagicMock(return_value='folder-test')
        real_save=Documento.save
        def fail_final(instance,*args,**kwargs):
            if 'drive_file_id' in kwargs.get('update_fields',[]):raise DatabaseError('private DB error')
            return real_save(instance,*args,**kwargs)
        original=sincronizar_documento.run.__wrapped__
        with patch('masiscam.services.GoogleDriveService',return_value=service),patch.object(Documento,'save',fail_final):
            with self.assertRaises(DriveSyncError):original(self.documento.pk)
        self.documento.refresh_from_db();self.assertEqual(self.documento.drive_upload_id,'stable-upload-id');self.assertFalse(self.documento.drive_file_id)
        self.assertNotIn('private',self.documento.error_sincronizacion)
        with patch('masiscam.services.GoogleDriveService',return_value=service):self.assertEqual(original(self.documento.pk),'stable-upload-id')
        self.assertEqual(len(remote),1);self.assertEqual(api.files().create.call_count,1)
        self.documento.refresh_from_db();self.assertEqual(self.documento.estado_sincronizacion,'SINCRONIZADO')


class ProductionSettingsTests(TestCase):
    def check_settings(self,**overrides):
        env=os.environ.copy();env.update(DEBUG='false',SECRET_KEY='simulation-only-key-'+('x'*80),ALLOWED_HOSTS='example.invalid,localhost,127.0.0.1',MASISCAM_PUBLIC_BASE_URL='https://example.invalid',DATABASE_URL='postgres://test:test@127.0.0.1:5432/test')
        env.update(overrides)
        return subprocess.run([sys.executable,'-c','import django; django.setup()'],env=dict(env,DJANGO_SETTINGS_MODULE='config.settings'),capture_output=True,text=True)

    def test_missing_db_fails_and_sqlite_rejected(self):
        self.assertNotEqual(self.check_settings(DATABASE_URL='').returncode,0)
        self.assertNotEqual(self.check_settings(DATABASE_URL='sqlite:///:memory:').returncode,0)
        self.assertEqual(self.check_settings().returncode,0)

    def test_public_origin_must_be_valid_https_host(self):
        for origin in ["http://example.invalid", "https://localhost", "https://not-allowed.invalid", "https://user:password@example.invalid", "https://example.invalid/path"]:
            self.assertNotEqual(self.check_settings(MASISCAM_PUBLIC_BASE_URL=origin).returncode,0)

    def test_missing_key_and_wildcard_hosts_rejected(self):
        self.assertNotEqual(self.check_settings(SECRET_KEY='').returncode,0)
        self.assertNotEqual(self.check_settings(ALLOWED_HOSTS='*').returncode,0)
