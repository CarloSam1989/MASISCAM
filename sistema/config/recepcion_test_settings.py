"""Isolated test DB only. No deployment env, Drive, broker or real media writes."""
import os
import tempfile
from pathlib import Path

from .test_settings import *  # noqa: F401,F403

# Legacy integration tests explicitly mock Drive and expect it enabled. Empty
# credentials prevent any unmocked service from reaching the network. The new
# reception tests override this to False.
GOOGLE_DRIVE_ENABLED = True
CELERY_TASK_ALWAYS_EAGER = False
MEDIA_ROOT = Path(tempfile.mkdtemp(prefix="masiscam-recepcion-tests-"))
if os.environ.get("MASISCAM_TEST_POSTGRES"):
    import environ
    DATABASES = {"default": environ.Env.db_url_config(os.environ["MASISCAM_TEST_POSTGRES"])}
    # Dedicated disposable cluster required; never reuse DATABASE_URL.
    if not DATABASES["default"]["NAME"].startswith("masiscam_qa_"):
        raise RuntimeError("La base de QA debe comenzar por masiscam_qa_.")
    if (DATABASES['default']['ENGINE']!='django.db.backends.postgresql'
            or DATABASES['default']['HOST'] not in {'127.0.0.1','localhost','::1'}):
        raise RuntimeError('QA PostgreSQL requiere instancia desechable en loopback; nunca una base remota.')
