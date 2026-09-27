from rest_framework.routers import DefaultRouter

from .views import LessonProgressViewSet

router = DefaultRouter()
router.register('', LessonProgressViewSet, basename='lessonprogress')

urlpatterns = router.urls
