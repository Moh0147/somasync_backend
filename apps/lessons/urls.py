from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import LessonSectionViewSet, LessonViewSet

lesson_router = DefaultRouter()
lesson_router.register('', LessonViewSet, basename='lesson')

section_router = DefaultRouter()
section_router.register('', LessonSectionViewSet, basename='lessonsection')

# 'sections/' must be listed before the lesson router's catch-all `<pk>/`
# pattern, or it would be swallowed as an attempted lesson pk (same
# collision-avoidance pattern as apps/exams/urls.py).
urlpatterns = [
    path('sections/', include(section_router.urls)),
    path('', include(lesson_router.urls)),
]
