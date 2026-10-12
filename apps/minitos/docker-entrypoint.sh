#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-True}" = "True" ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
fi
exec "$@"
