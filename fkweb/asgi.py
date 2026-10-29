"""ASGI entry point, used for the long-lived streams the WSGI workers can't hold.

The chart runs this under uvicorn in its own deployment, which the ingress
sends /api/playout/events to; everything else stays on gunicorn.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fkweb.settings.production")

application = get_asgi_application()
