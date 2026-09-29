"""
Base settings for the SomaSync project, shared by all environments.

Environment-specific settings live in development.py / production.py,
which both start with `from .base import *`.
"""

from datetime import timedelta
from pathlib import Path

from decouple import Csv, config

# config/settings/base.py -> config/settings -> config -> project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='', cast=Csv())


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',

    'apps.accounts',
    'apps.schools',
    'apps.academics',
    'apps.classes',
    'apps.lessons',
    'apps.quizzes',
    'apps.progress',
    'apps.sync',
    'apps.exams',
    'apps.ai',
    'apps.analytics',
]

MIDDLEWARE = [
    # First, so its response-processing (compression) runs LAST, after
    # every other middleware has already finished with the body -- per
    # Django's own placement guidance. Matters here specifically because
    # this project targets schools on slow/expensive/unreliable mobile
    # data; compressing JSON responses (which compress very well) cuts
    # real bytes over that link. BREACH-style compression-oracle attacks
    # are a risk for pages that reflect attacker input alongside a secret
    # in the same response (e.g. a CSRF token in HTML) -- this is a JSON
    # API authenticated via JWT bearer tokens, not cookies/CSRF, so that
    # risk doesn't apply here.
    'django.middleware.gzip.GZipMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('DB_NAME', default='somasync'),
        'USER': config('DB_USER', default='somasync'),
        'PASSWORD': config('DB_PASSWORD', default=''),
        'HOST': config('DB_HOST', default='localhost'),
        'PORT': config('DB_PORT', default='5432'),
        # Reuse a connection across requests instead of opening/closing a
        # new TCP+TLS handshake on every single one -- real overhead on
        # constrained hardware. 0 (Django's default) would be fine on a
        # fast local DB, but this project explicitly targets weak
        # deployment hardware, so pay the (tiny) risk of a stale pooled
        # connection to save that per-request cost.
        'CONN_MAX_AGE': config('DB_CONN_MAX_AGE', default=60, cast=int),
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'

# File uploads (lesson attachments, etc.) — stored on disk via Django's
# default FileSystemStorage, never as bytes in PostgreSQL. Swappable to
# object storage (S3 etc.) later purely via DEFAULT_FILE_STORAGE, with no
# model changes needed.
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Email (console backend by default; overridden per environment if needed)

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'


# Django REST Framework

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'common.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': 'common.exceptions.custom_exception_handler',
    # Baseline abuse guard for every endpoint, plus two tighter scoped
    # rates applied explicitly to login (brute force) and the AI
    # explain-mistake endpoint (protects the single shared Ollama
    # instance). Disabled during `manage.py test` (see development.py) --
    # otherwise test classes that fire many requests as the same
    # user/IP would start tripping these unrelated to what they test.
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': '30/min',
        'user': '120/min',
        'login': '5/min',
        'ai_explain': '10/min',
    },
}

# Separate kill switch (not part of REST_FRAMEWORK) for the two views that
# apply their own throttle explicitly (LoginView, AIExplanationRequestViewSet)
# rather than going through DEFAULT_THROTTLE_CLASSES -- see development.py.
THROTTLING_ENABLED = True


# Simple JWT
# https://django-rest-framework-simplejwt.readthedocs.io/

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=config('JWT_ACCESS_MINUTES', default=30, cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=config('JWT_REFRESH_DAYS', default=7, cast=int)),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}


# CORS
# https://github.com/adamchainz/django-cors-headers

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='', cast=Csv())


# AI service (Ollama) — see apps/ai. Ollama is NEVER exposed directly to
# the frontend; only reachable through AIService implementations, which
# every AI feature (Phase 13+) must go through.

OLLAMA_BASE_URL = config('OLLAMA_BASE_URL', default='http://localhost:11434')
OLLAMA_MODEL = config('OLLAMA_MODEL', default='qwen2.5:3b')
OLLAMA_TIMEOUT_SECONDS = config('OLLAMA_TIMEOUT_SECONDS', default=90, cast=int)
