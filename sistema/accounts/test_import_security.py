import io
import json
from pathlib import Path
import tempfile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.contrib.auth import get_user_model
from accounts.models import Empresa


class ImportSecurityTests(TestCase):
    def sample(self):
        return [{"model":"core.usuario","pk":99,"fields":{"username":"historical-user","password":"!","is_staff":True,"is_superuser":True}},
                {"model":"core.empresa","pk":99,"fields":{"nombre":"Historical company"}},
                {"model":"core.perfil","pk":99,"fields":{"user":99,"empresa":99}},
                {"model":"masiscam.rolmasiscam","pk":99,"fields":{"perfil":99,"rol":"ADMIN","activo":True}}]

    def run_import(self,data,**kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'selected.json';path.write_text(json.dumps(data),encoding='utf-8')
            output=io.StringIO();call_command('importar_syscloud',str(path),stdout=output,**kwargs)
            return output.getvalue()

    def test_dry_run_does_not_write_or_reserve_sequences(self):
        with self.assertNumQueries(4):output=self.run_import(self.sample(),dry_run=True)
        self.assertFalse(Empresa.objects.exists());self.assertFalse(get_user_model().objects.exists())
        self.assertIn('auth.user: 1',output);self.assertIn('sin escribir',output)

    def test_privileges_downgraded_unless_explicit(self):
        self.run_import(self.sample(),apply=True)
        user=get_user_model().objects.get(pk=99);self.assertFalse(user.is_superuser);self.assertFalse(user.is_staff)

    def test_explicit_privileges_and_company_allowlist(self):
        self.run_import(self.sample(),apply=True,allow_superusers=True,empresa_id=[99])
        self.assertTrue(get_user_model().objects.get(pk=99).is_superuser)

    def test_missing_fk_duplicate_unknown_model_and_unapproved_company(self):
        for data,options in [(self.sample()[:-1]+[{"model":"masiscam.rolmasiscam","pk":99,"fields":{"perfil":100,"rol":"ADMIN"}}],{}),
                             (self.sample()+[self.sample()[0]],{}),
                             ([{"model":"core.factura","pk":1,"fields":{}}],{}),
                             (self.sample(),{"empresa_id":[1]})]:
            with self.assertRaises(CommandError):self.run_import(data,apply=True,**options)
            self.assertFalse(Empresa.objects.exists());self.assertFalse(get_user_model().objects.exists())

    def test_malformed_json_structure_rejected(self):
        for data in [[],{},[{"model":"core.usuario","pk":"99","fields":{}}]]:
            with self.assertRaises(CommandError):self.run_import(data)
