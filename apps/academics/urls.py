from rest_framework.routers import DefaultRouter

from .views import ClassLevelViewSet, LearningObjectiveViewSet, SubjectViewSet, TopicViewSet

router = DefaultRouter()
router.register('class-levels', ClassLevelViewSet, basename='classlevel')
router.register('subjects', SubjectViewSet, basename='subject')
router.register('topics', TopicViewSet, basename='topic')
router.register('learning-objectives', LearningObjectiveViewSet, basename='learningobjective')

urlpatterns = router.urls
