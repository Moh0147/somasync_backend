from django.conf import settings
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView as BaseTokenRefreshView

from common.throttling import LoginRateThrottle
from common.utils import api_response

from .serializers import CustomTokenObtainPairSerializer, LogoutSerializer, UserSerializer


class LoginView(TokenObtainPairView):
    """POST /api/auth/login/ — issues an access/refresh token pair plus a
    snapshot of the authenticated user's profile."""

    serializer_class = CustomTokenObtainPairSerializer
    throttle_scope = 'login'

    def get_throttles(self):
        # Applied explicitly (not via DEFAULT_THROTTLE_CLASSES) so it can't
        # be bypassed by a view accidentally overriding throttle_classes --
        # gated on THROTTLING_ENABLED so test mode can switch it off without
        # affecting DEFAULT_THROTTLE_CLASSES (see development.py).
        if not settings.THROTTLING_ENABLED:
            return []
        return [LoginRateThrottle()]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        return api_response(data=serializer.validated_data, message='Login successful')


class TokenRefreshView(BaseTokenRefreshView):
    """POST /api/auth/refresh/ — exchanges a refresh token for a new access token."""

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        return api_response(data=serializer.validated_data, message='Token refreshed')


class MeView(APIView):
    """GET /api/auth/me/ — the current authenticated user's profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return api_response(data=UserSerializer(request.user).data)


class LogoutView(APIView):
    """POST /api/auth/logout/ — blacklists the given refresh token so it
    can never be exchanged for a new access token again. Without this, a
    "logged out" refresh token would stay valid for the rest of its
    lifetime (JWT_REFRESH_DAYS) even after the user logs out client-side —
    this closes that window immediately. Does not touch any still-live
    access token, which simply expires on its own short lifetime."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data['refresh']).blacklist()
        except TokenError as e:
            raise ValidationError({'refresh': str(e)})
        return api_response(message='Logged out')
