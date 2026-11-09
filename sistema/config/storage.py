"""Public assets have their own permissions; uploaded documents stay private."""
import os
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.core.exceptions import ImproperlyConfigured


class PublicStaticFilesStorage(StaticFilesStorage):
    def __init__(self, *args, **kwargs):
        kwargs.update(file_permissions_mode=0o644, directory_permissions_mode=0o755)
        super().__init__(*args, **kwargs)
        root = Path(self.location).resolve()
        if settings.MEDIA_ROOT:
            media = Path(settings.MEDIA_ROOT).resolve()
            if root == media or root in media.parents or media in root.parents:
                raise ImproperlyConfigured('STATIC_ROOT y MEDIA_ROOT deben ser directorios separados.')

    def post_process(self, paths, dry_run=False, **options):
        # collectstatic skips unchanged files, including their old 0600 modes.
        # Repair only discovered public assets and their directories, never links.
        if dry_run:
            return
        root = Path(self.location)
        root.mkdir(mode=self.directory_permissions_mode, parents=True, exist_ok=True)
        directories = {root}
        files = []
        for name in paths:
            path = Path(self.path(name))
            files.append((name, path))
            directories.update(parent for parent in path.parents if parent == root or root in parent.parents)
        for path in directories | {path for _, path in files}:
            if path.is_symlink():
                raise ImproperlyConfigured('Los estáticos públicos no pueden contener enlaces simbólicos.')
        for directory in directories:
            os.chmod(directory, self.directory_permissions_mode)
        for name, path in files:
            os.chmod(path, self.file_permissions_mode)
            yield name, name, False
