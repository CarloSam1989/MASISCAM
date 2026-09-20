"""Deployment check with an isolated simulated environment; no DB/API connection."""
import os
from pathlib import Path
import subprocess
import sys

env=os.environ.copy()
env.update(DEBUG="false",SECRET_KEY="simulation-only-not-a-credential-"+"x"*64,
    ALLOWED_HOSTS="example.invalid,localhost,127.0.0.1",CSRF_TRUSTED_ORIGINS="https://example.invalid",
    MASISCAM_PUBLIC_BASE_URL="https://example.invalid",DATABASE_URL="postgres://simulation:simulation@127.0.0.1:5432/simulation",
    SECURE_SSL_REDIRECT="true",SESSION_COOKIE_SECURE="true",CSRF_COOKIE_SECURE="true",TRUST_PROXY="true",
    SECURE_HSTS_SECONDS="300",SECURE_HSTS_INCLUDE_SUBDOMAINS="false",SECURE_HSTS_PRELOAD="false",
    GOOGLE_DRIVE_ENABLED="false",DJANGO_SETTINGS_MODULE="config.settings")
root=Path(__file__).resolve().parent.parent
raise SystemExit(subprocess.call([sys.executable,str(root/"manage.py"),"check","--deploy"],env=env,cwd=root))
