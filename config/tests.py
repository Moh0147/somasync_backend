import os
import subprocess
import sys

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

class ProjectFoundationTests(TestCase):
    def test_project_settings_load(self):
        self.assertTrue(settings.configured)
        self.assertIn('rest_framework', settings.INSTALLED_APPS)
        self.assertIn('corsheaders', settings.INSTALLED_APPS)

    def test_all_domain_apps_registered(self):
        for app in [
            'apps.accounts', 'apps.schools', 'apps.academics', 'apps.classes',
            'apps.lessons', 'apps.quizzes', 'apps.progress', 'apps.sync',
            'apps.exams', 'apps.ai', 'apps.analytics',
        ]:
            self.assertIn(app, settings.INSTALLED_APPS)

    def test_database_engine_is_postgresql(self):
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.postgresql')

class DatabaseConnectionTests(TestCase):
    def test_database_connection_is_usable(self):
        connection.ensure_connection()
        self.assertTrue(connection.is_usable())

class HealthEndpointTests(TestCase):
    def test_health_endpoint_returns_ok(self):
        response = APIClient().get('/api/health/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        self.assertEqual(response.data['data']['status'], 'ok')
        self.assertEqual(response.data['data']['database'], 'connected')

    def test_health_endpoint_does_not_require_auth(self):
        response = APIClient().get('/api/health/')
        self.assertNotEqual(response.status_code, 401)

class JWTConfigurationTests(TestCase):
    def test_simple_jwt_settings_present(self):
        self.assertIn('ACCESS_TOKEN_LIFETIME', settings.SIMPLE_JWT)
        self.assertIn('REFRESH_TOKEN_LIFETIME', settings.SIMPLE_JWT)
        self.assertEqual(settings.SIMPLE_JWT['AUTH_HEADER_TYPES'], ('Bearer',))

    def test_jwt_token_can_be_issued_and_resolves_user(self):
        User = get_user_model()
        user = User.objects.create_user(
            username='jwt-tester', password='pass12345',
            role=User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM,
        )
        refresh = RefreshToken.for_user(user)

        authenticator = JWTAuthentication()
        validated_token = authenticator.get_validated_token(str(refresh.access_token))
        resolved_user = authenticator.get_user(validated_token)

        self.assertEqual(resolved_user, user)

class BasicAPITests(TestCase):
    def test_admin_login_page_loads(self):
        response = APIClient().get('/admin/login/')
        self.assertEqual(response.status_code, 200)

    def test_unknown_endpoint_returns_404(self):
        response = APIClient().get('/api/does-not-exist/')
        self.assertEqual(response.status_code, 404)

class ExceptionHandlerTests(TestCase):
    def test_custom_exception_handler_wraps_drf_errors(self):
        from rest_framework.exceptions import AuthenticationFailed

        from common.exceptions import custom_exception_handler

        response = custom_exception_handler(AuthenticationFailed('Invalid token'), {})

        self.assertIsNotNone(response)
        self.assertFalse(response.data['success'])
        self.assertIn('errors', response.data)

class ProductionSettingsTests(TestCase):
    """Phase 20's production.py boot-time guards (weak SECRET_KEY, missing
    ALLOWED_HOSTS) had never actually been exercised by the automated
    suite -- `manage.py test` always loads development settings, so these
    were only ever checked manually. production.py is loaded fresh in a
    subprocess for each case, since Django settings can't be safely
    re-imported with different env vars inside an already-running test
    process."""

    def _boot(self, **env_overrides):
        env = {
            'PATH': os.environ.get('PATH', ''),
            'DJANGO_SETTINGS_MODULE': 'config.settings.production',
            'SECRET_KEY': 'x' * 50,
            'DEBUG': 'False',
            'ALLOWED_HOSTS': 'example.com',
            'DB_PASSWORD': 'irrelevant-not-connected-to',
        }
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, '-c', 'import django; django.setup(); print("BOOT_OK")'],
            capture_output=True, text=True, env=env, cwd=str(settings.BASE_DIR), timeout=30,
        )

    def test_boots_with_strong_secret_key_and_allowed_hosts(self):
        result = self._boot()
        self.assertIn('BOOT_OK', result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_refuses_to_boot_with_placeholder_secret_key(self):
        result = self._boot(SECRET_KEY='change-me')
        self.assertNotIn('BOOT_OK', result.stdout)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('SECRET_KEY', result.stderr)

    def test_refuses_to_boot_with_short_secret_key(self):
        result = self._boot(SECRET_KEY='short')
        self.assertNotIn('BOOT_OK', result.stdout)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('SECRET_KEY', result.stderr)

    def test_refuses_to_boot_without_allowed_hosts(self):
        result = self._boot(ALLOWED_HOSTS='')
        self.assertNotIn('BOOT_OK', result.stdout)
        self.assertIn('ImproperlyConfigured', result.stderr)
        self.assertIn('ALLOWED_HOSTS', result.stderr)
