from rest_framework.routers import DefaultRouter

from .views import SchoolClassViewSet

router = DefaultRouter()
router.register('', SchoolClassViewSet, basename='schoolclass')

urlpatterns = router.urls
