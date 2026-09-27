from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ExamQuestionViewSet, ExamResultViewSet, ExamScriptViewSet, ExamViewSet, StudentExamAnswerViewSet,
)

exam_router = DefaultRouter()
exam_router.register('', ExamViewSet, basename='exam')

question_router = DefaultRouter()
question_router.register('', ExamQuestionViewSet, basename='examquestion')

answer_router = DefaultRouter()
answer_router.register('', StudentExamAnswerViewSet, basename='examanswer')

script_router = DefaultRouter()
script_router.register('', ExamScriptViewSet, basename='examscript')

result_router = DefaultRouter()
result_router.register('', ExamResultViewSet, basename='examresult')

# 'questions/', 'answers/', 'scripts/' and 'results/' must be listed before
# the exam router's catch-all `<pk>/` pattern, or they'd be swallowed as an
# attempted exam pk.
urlpatterns = [
    path('questions/', include(question_router.urls)),
    path('answers/', include(answer_router.urls)),
    path('scripts/', include(script_router.urls)),
    path('results/', include(result_router.urls)),
    path('', include(exam_router.urls)),
]
