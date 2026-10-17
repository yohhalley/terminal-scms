#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-True}" = "True" ]; then
    python manage.py migrate --noinput
    if [ -n "${SIMULATOR_MINITOS_USERNAME:-}" ] || [ -n "${SIMULATOR_MINITOS_PASSWORD:-}" ]; then
        if [ -z "${SIMULATOR_MINITOS_USERNAME:-}" ] || [ -z "${SIMULATOR_MINITOS_PASSWORD:-}" ]; then
            echo "Both SIMULATOR_MINITOS_USERNAME and SIMULATOR_MINITOS_PASSWORD must be configured." >&2
            exit 1
        fi
        python manage.py ensure_simulator_user
    else
        echo "Simulator credentials are not configured; skipping MiniTOS simulator user bootstrap."
    fi
    python manage.py collectstatic --noinput
fi
exec "$@"
