from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import QuestionViewSet, QuizViewSet, StudentQuizAttemptViewSet

quiz_router = DefaultRouter()
quiz_router.register('', QuizViewSet, basename='quiz')

question_router = DefaultRouter()
question_router.register('', QuestionViewSet, basename='question')

attempt_router = DefaultRouter()
attempt_router.register('', StudentQuizAttemptViewSet, basename='quizattempt')

# 'questions/' and 'attempts/' must be listed before the quiz router's
# catch-all `<pk>/` pattern, or they'd be swallowed as an attempted quiz pk.
urlpatterns = [
    path('questions/', include(question_router.urls)),
    path('attempts/', include(attempt_router.urls)),
    path('', include(quiz_router.urls)),
]
