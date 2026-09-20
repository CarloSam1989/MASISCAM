from pathlib import Path
import os
import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)
DEBUG = env.bool("DEBUG", default=False)
SECRET_KEY = env("SECRET_KEY", default="dev-only-masiscam" if DEBUG else environ.Env.NOTSET)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "accounts", "masiscam.apps.MasiscamConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware", "accounts.middleware.EmpresaActivaMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
if DEBUG:
    DATABASES = {"default": env.db("DATABASE_URL", default="sqlite:///" + str(BASE_DIR / "db.sqlite3"))}
else:
    DATABASES = {"default": env.db("DATABASE_URL")}
    if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("Producción requiere DATABASE_URL de PostgreSQL.")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured("Producción requiere ALLOWED_HOSTS explícitos.")
    if not SECRET_KEY or SECRET_KEY.startswith(("replace-", "__")):
        raise ImproperlyConfigured("Configure una SECRET_KEY privada en producción.")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "es-ec"
TIME_ZONE = env("TIME_ZONE", default="America/Guayaquil")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "masiscam:dashboard"
LOGOUT_REDIRECT_URL = "web:home"
SESSION_COOKIE_NAME = "masiscam_sessionid"
CSRF_COOKIE_NAME = "masiscam_csrftoken"
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_REDIRECT_EXEMPT = [r"^health/$"]
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=not DEBUG)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=not DEBUG)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
# Activar solo cuando el proxy confiable reemplace siempre X-Forwarded-Proto.
if env.bool("TRUST_PROXY", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
MASISCAM_PUBLIC_BASE_URL = env("MASISCAM_PUBLIC_BASE_URL", default="http://localhost:8000" if DEBUG else environ.Env.NOTSET).rstrip("/")
if not DEBUG:
    from urllib.parse import urlsplit
    public_origin = urlsplit(MASISCAM_PUBLIC_BASE_URL)
    if (public_origin.scheme != "https" or not public_origin.hostname
            or public_origin.hostname not in ALLOWED_HOSTS
            or public_origin.hostname in {"localhost", "127.0.0.1"}
            or "__" in public_origin.hostname or public_origin.username or public_origin.password
            or public_origin.path or public_origin.query or public_origin.fragment):
        raise ImproperlyConfigured("Producción requiere MASISCAM_PUBLIC_BASE_URL HTTPS con host permitido y sin ruta ni credenciales.")
MASISCAM_MAX_UPLOAD_MB = env.int("MASISCAM_MAX_UPLOAD_MB", default=25)
DATA_UPLOAD_MAX_MEMORY_SIZE = MASISCAM_MAX_UPLOAD_MB * 1024 * 1024
GOOGLE_DRIVE_ENABLED = env.bool("GOOGLE_DRIVE_ENABLED", default=True)
GOOGLE_DRIVE_CREDENTIALS_FILE = env("GOOGLE_DRIVE_CREDENTIALS_FILE", default="")
GOOGLE_DRIVE_ROOT_FOLDER_ID = env("GOOGLE_DRIVE_ROOT_FOLDER_ID", default="")
GOOGLE_DRIVE_SHARED_DRIVE_ID = env("GOOGLE_DRIVE_SHARED_DRIVE_ID", default="")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/1")
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SOFT_TIME_LIMIT = 270
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600, "socket_connect_timeout": 5, "socket_timeout": 5}
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}}, "root": {"handlers": ["console"], "level": "INFO"}}
