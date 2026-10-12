"""Development settings. Loaded via DJANGO_SETTINGS_MODULE=config.settings.development"""

import sys
import tempfile

from .base import *  # noqa: F401,F403

DEBUG = True

if not ALLOWED_HOSTS:  # noqa: F405
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']

REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []  # noqa: F405
THROTTLING_ENABLED = False

if 'test' in sys.argv:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    MEDIA_ROOT = tempfile.mkdtemp(prefix='somasync_test_media_')
    REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] = []
    THROTTLING_ENABLED = False
