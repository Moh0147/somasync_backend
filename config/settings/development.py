"""Development settings. Loaded via DJANGO_SETTINGS_MODULE=config.settings.development"""

import sys
import tempfile

from .base import *  # noqa: F401,F403

DEBUG = True

if not ALLOWED_HOSTS:  # noqa: F405
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']

if 'test' in sys.argv:
    # Production-strength PBKDF2 iterations make test suites creating many
    # users extremely slow. Never applies to real dev/login traffic.
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    # File uploads in tests (lesson attachments, etc.) aren't rolled back
    # like DB transactions — without this they'd permanently litter the
    # real media/ directory on every test run.
    MEDIA_ROOT = tempfile.mkdtemp(prefix='somasync_test_media_')
    # Off by default so ordinary tests firing many requests as the same
    # user/IP don't start tripping Phase 20's throttle rates. Individual
    # throttle tests re-enable it explicitly via @override_settings.
    REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []
    THROTTLING_ENABLED = False
