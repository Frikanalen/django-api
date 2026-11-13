#!/usr/bin/env sh

./manage.py collectstatic --noinput
./manage.py migrate

# Create superuser if environment variables are set (and skip if already exists)
if [ -n "$DJANGO_SUPERUSER_EMAIL" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
  ./manage.py createsuperuser --noinput || true
fi

if [ "$DJANGO_LOAD_FIXTURES" = "true" ]; then
  ./manage.py loaddata frikanalen || true
fi

# Threads, so one slow request does not hold up every other one. The signal
# report needs at least two: Django asks MediaMTX, and MediaMTX asks Django
# whether that is allowed before it answers.
gunicorn fkweb.wsgi:application --bind 0.0.0.0:8080 --threads "${GUNICORN_THREADS:-8}"
