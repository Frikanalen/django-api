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

gunicorn fkweb.wsgi:application --bind 0.0.0.0:8080
