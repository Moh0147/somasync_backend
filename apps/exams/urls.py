from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AuditLogViewSet, ExamQuestionViewSet, ExamResultViewSet, ExamScriptViewSet,
    ExamViewSet, StudentExamAnswerViewSet,
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

audit_router = DefaultRouter()
audit_router.register('', AuditLogViewSet, basename='markingauditlog')

urlpatterns = [
    path('questions/', include(question_router.urls)),
    path('answers/', include(answer_router.urls)),
    path('scripts/', include(script_router.urls)),
    path('results/', include(result_router.urls)),
    path('audit/', include(audit_router.urls)),
    path('', include(exam_router.urls)),
]
