"""Opt-in local sandbox: own persistent SQLite/media; no real Drive or broker.

Use explicitly with --settings=config.recepcion_local_settings. Never import in
production. No migrations or server are started by importing these settings.
"""
from .settings import *  # noqa: F401,F403

DEBUG = True
MASISCAM_RECEPCION_LOCAL = True
MASISCAM_TECNICA_LOCAL = True
MASISCAM_OPERACION_LOCAL = True
MASISCAM_CALIBRACION_LOCAL = True
MASISCAM_CIERRE_LOCAL = True
GOOGLE_DRIVE_ENABLED = False
GOOGLE_DRIVE_CREDENTIALS_FILE = ""
GOOGLE_DRIVE_ROOT_FOLDER_ID = ""
GOOGLE_DRIVE_SHARED_DRIVE_ID = ""
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
_recepcion_local_root = BASE_DIR / ".local-recepcion"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": _recepcion_local_root / "sandbox.sqlite3"}}
MEDIA_ROOT = _recepcion_local_root / "media"
