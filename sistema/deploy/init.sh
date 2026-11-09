#!/bin/sh
set -eu
# Schema changes are a separate, reviewed deployment step after backup.
# Starting/restarting Compose must never apply pending migrations implicitly.
python manage.py migrate --check
# Public static permissions (including unchanged files) are handled by storage.
# Do not relax the process umask or touch private media/secrets.
python manage.py collectstatic --noinput
