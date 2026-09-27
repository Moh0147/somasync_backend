from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AIExplanationRequestViewSet, ai_health_check

router = DefaultRouter()
router.register('explain-mistake', AIExplanationRequestViewSet, basename='explain-mistake')

urlpatterns = [
    path('health/', ai_health_check, name='ai-health'),
    path('', include(router.urls)),
]
