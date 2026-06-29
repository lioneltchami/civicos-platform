"""
ASGI config for Govstack.

Used by uvicorn or Daphne for async support (WebSockets, long-polling).
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()
