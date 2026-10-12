"""Production settings. Loaded via DJANGO_SETTINGS_MODULE=config.settings.production"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured('ALLOWED_HOSTS must be set via the environment in production.')

if SECRET_KEY == 'change-me' or len(SECRET_KEY) < 32:  # noqa: F405
    raise ImproperlyConfigured(
        'SECRET_KEY must be a strong, unique value (32+ chars) in production -- '
        'generate one with `python -c "import secrets; print(secrets.token_urlsafe(50))"`.',
    )

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
