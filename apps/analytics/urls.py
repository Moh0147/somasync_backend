from django.urls import path

from . import views

urlpatterns = [
    path('teacher/classes/', views.teacher_class_performance, name='analytics-teacher-classes'),
    path('teacher/students/', views.teacher_student_performance, name='analytics-teacher-students'),
    path('teacher/quizzes/', views.teacher_quiz_performance, name='analytics-teacher-quizzes'),
    path(
        'teacher/quizzes/<int:quiz_id>/questions/', views.teacher_question_performance,
        name='analytics-teacher-questions',
    ),
    path('teacher/lessons/', views.teacher_lesson_completion, name='analytics-teacher-lessons'),
    path('teacher/exams/', views.teacher_exam_performance, name='analytics-teacher-exams'),

    path('school/overview/', views.school_overview, name='analytics-school-overview'),
    path('school/classes/', views.school_class_performance, name='analytics-school-classes'),
    path('school/subjects/', views.school_subject_performance, name='analytics-school-subjects'),

    path('system/schools/', views.system_schools, name='analytics-system-schools'),
    path('system/users/', views.system_users, name='analytics-system-users'),
    path('system/user-directory/', views.system_user_directory, name='analytics-system-user-directory'),
    path('system/usage/', views.system_usage, name='analytics-system-usage'),
]
