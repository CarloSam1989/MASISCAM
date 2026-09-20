import os
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("SECRET_KEY", "test-only-key")
os.environ["DEBUG"] = "true"
from .settings import *

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
GOOGLE_DRIVE_CREDENTIALS_FILE = ""
GOOGLE_DRIVE_ROOT_FOLDER_ID = ""
MEDIA_ROOT = BASE_DIR / ".test-media"

LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"null": {"class": "logging.NullHandler"}}, "loggers": {"django.request": {"handlers": ["null"], "propagate": False}}}
