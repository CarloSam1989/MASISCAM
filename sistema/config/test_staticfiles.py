import os
import stat
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management import call_command
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from .storage import PublicStaticFilesStorage


class StaticFilesTests(SimpleTestCase):
    def test_public_storage_is_separate_from_private_uploads(self):
        self.assertEqual(settings.STATIC_URL, '/static/')
        self.assertEqual(settings.STATIC_ROOT, settings.BASE_DIR / 'staticfiles')
        self.assertIsInstance(storages['staticfiles'], PublicStaticFilesStorage)
        self.assertEqual(storages['staticfiles'].file_permissions_mode, 0o644)
        self.assertEqual(storages['staticfiles'].directory_permissions_mode, 0o755)
        self.assertEqual(storages['default'].file_permissions_mode, 0o640)
        self.assertEqual(storages['default'].directory_permissions_mode, 0o750)

    def test_collectstatic_repairs_unchanged_files_and_keeps_private_modes(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            source, public, private = base / 'source', base / 'public', base / 'private'
            source.mkdir()
            (source / 'css').mkdir()
            (source / 'css' / 'taller.css').write_text('body{color:#16283d}', encoding='utf-8')
            with override_settings(STATIC_ROOT=public, STATICFILES_DIRS=[source], MEDIA_ROOT=private,
                    STATICFILES_FINDERS=['django.contrib.staticfiles.finders.FileSystemFinder']):
                old_umask = os.umask(0o077)
                try:
                    storages['default'].save('documentos/privado.txt', ContentFile(b'private QA'))
                    private_file = private / 'documentos' / 'privado.txt'
                    before = (private_file.stat().st_mode, private_file.parent.stat().st_mode)
                    call_command('collectstatic', interactive=False, verbosity=0)
                    target = public / 'css' / 'taller.css'
                    original = (target.read_bytes(), target.stat().st_mtime_ns)
                    target.chmod(0o600)
                    target.parent.chmod(0o700)
                    public.chmod(0o700)
                    output = StringIO()
                    call_command('collectstatic', interactive=False, verbosity=1, stdout=output)
                    self.assertIn('0 static files copied', output.getvalue())
                    self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), original)
                    self.assertEqual(before, (private_file.stat().st_mode, private_file.parent.stat().st_mode))
                    if os.name == 'posix':
                        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)
                        self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o755)
                        self.assertEqual(stat.S_IMODE(public.stat().st_mode), 0o755)
                    target.chmod(0o600)
                    before_dry_run = target.stat().st_mode
                    call_command('collectstatic', interactive=False, dry_run=True, verbosity=0)
                    self.assertEqual(target.stat().st_mode, before_dry_run)
                finally:
                    os.umask(old_umask)
                    # Leave temporary files writable for Windows cleanup.
                    target = public / 'css' / 'taller.css'
                    if target.exists():
                        target.chmod(0o644)

    def test_static_root_cannot_overlap_private_media(self):
        with TemporaryDirectory() as temp, override_settings(MEDIA_ROOT=Path(temp) / 'private'):
            for root in (Path(temp), Path(temp) / 'private', Path(temp) / 'private' / 'nested'):
                with self.subTest(root=root), self.assertRaises(ImproperlyConfigured):
                    PublicStaticFilesStorage(location=root)

    @skipUnless(os.name == 'posix', 'POSIX symlink/mode protection requires Linux')
    def test_post_processing_rejects_symlinks_without_chmod_private_files(self):
        with TemporaryDirectory() as temp:
            base = Path(temp)
            public, private = base / 'public', base / 'private'
            public.mkdir()
            private.write_bytes(b'private QA')
            private.chmod(0o600)
            (public / 'linked.css').symlink_to(private)
            storage = PublicStaticFilesStorage(location=public)
            with self.assertRaises(ImproperlyConfigured):
                list(storage.post_process({'linked.css': (None, None)}))
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
