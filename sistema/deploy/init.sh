#!/bin/sh
set -eu
# Schema changes are a separate, reviewed deployment step after backup.
# Starting/restarting Compose must never apply pending migrations implicitly.
python manage.py migrate --check
python manage.py collectstatic --noinput
chmod -R a+rX staticfiles
