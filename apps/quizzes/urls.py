from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import QuestionViewSet, QuizViewSet, StudentQuizAttemptViewSet

quiz_router = DefaultRouter()
quiz_router.register('', QuizViewSet, basename='quiz')

question_router = DefaultRouter()
question_router.register('', QuestionViewSet, basename='question')

attempt_router = DefaultRouter()
attempt_router.register('', StudentQuizAttemptViewSet, basename='quizattempt')

urlpatterns = [
    path('questions/', include(question_router.urls)),
    path('attempts/', include(attempt_router.urls)),
    path('', include(quiz_router.urls)),
]
