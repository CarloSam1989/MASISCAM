#!/bin/sh
set -eu
python manage.py migrate --noinput
python manage.py collectstatic --noinput
chmod -R a+rX staticfiles
