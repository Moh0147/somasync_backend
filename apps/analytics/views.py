"""Read-only reporting endpoints. No models of its own -- everything here
aggregates data already built by every prior phase.

Query discipline: every aggregate here is either (a) a single .aggregate()
call scoped via a subquery filter (student_id__in=...), or (b) an
.annotate() combining multiple Count()/Avg() over the SAME relation
(safe — Django's classic multi-relation annotate fan-out bug only bites
when combining aggregates across DIFFERENT relations in one call, which
this file deliberately avoids by never doing that). The outer loop in
each "list one row per X" endpoint is always bounded by a small,
naturally-limited set (a teacher's own classes/quizzes/lessons/exams, or
a school's own subjects/classes) — never by student/attempt count, which
is where real N+1 risk would live.
"""

from django.contrib.auth import get_user_model
from django.db.models import Avg, Count, Q
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.academics.models import Subject
from apps.ai.models import AIExplanationRequest
from apps.classes.models import SchoolClass, StudentClassMembership
from apps.exams.models import Exam, ExamCandidate, ExamScript, StudentExamAttempt
from apps.lessons.models import Lesson
from apps.progress.models import LessonProgress
from apps.quizzes.models import Question, Quiz, StudentQuizAttempt
from apps.schools.models import School
from apps.sync.models import SyncRecord
from common.permissions import IsSchoolAdmin, IsSystemAdmin, IsTeacher
from common.utils import api_response

User = get_user_model()

def _avg_quiz_percentage_for_students(student_ids):
    return StudentQuizAttempt.objects.filter(
        student_id__in=student_ids, percentage__isnull=False,
    ).aggregate(avg=Avg('percentage'))['avg']

def _avg_exam_percentage_for_students(student_ids):
    return StudentExamAttempt.objects.filter(
        student_id__in=student_ids, percentage__isnull=False,
    ).aggregate(avg=Avg('percentage'))['avg']

def _lesson_completion_rate_for_students(student_ids):
    stats = LessonProgress.objects.filter(student_id__in=student_ids).aggregate(
        total=Count('id'), completed=Count('id', filter=Q(completed=True)),
    )
    if not stats['total']:
        return None
    return round(stats['completed'] / stats['total'] * 100, 2)

def _class_summary(school_class):
    student_ids = StudentClassMembership.objects.filter(
        school_class=school_class,
    ).values_list('student_id', flat=True)
    return {
        'class_id': school_class.id,
        'class_name': school_class.name,
        'academic_year': school_class.academic_year,
        'student_count': StudentClassMembership.objects.filter(school_class=school_class).count(),
        'avg_quiz_percentage': _avg_quiz_percentage_for_students(student_ids),
        'avg_exam_percentage': _avg_exam_percentage_for_students(student_ids),
    }

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_class_performance(request):
    """Class average: one row per class this teacher is assigned to."""
    classes = SchoolClass.objects.filter(teacher_memberships__teacher=request.user).distinct()
    return api_response(data=[_class_summary(c) for c in classes])

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_student_performance(request):
    """Student performance: one row per student across this teacher's
    classes (optionally narrowed to one class via ?class_id=).

    Grouped aggregation, not a per-student loop: caught via an empirical
    query-count check that an earlier per-student version of this was a
    real N+1 (3 extra queries x every student). Now it's a fixed ~5
    queries total regardless of how many students there are."""
    class_id = request.query_params.get('class_id')
    class_ids = SchoolClass.objects.filter(teacher_memberships__teacher=request.user).values_list('id', flat=True)
    if class_id:
        if int(class_id) not in set(class_ids):
            raise ValidationError({'class_id': 'Not one of your classes.'})
        class_ids = [int(class_id)]

    student_ids = list(
        StudentClassMembership.objects.filter(school_class_id__in=class_ids)
        .values_list('student_id', flat=True).distinct()
    )

    quiz_avg_by_student = {
        row['student_id']: row['avg'] for row in
        StudentQuizAttempt.objects.filter(student_id__in=student_ids, percentage__isnull=False)
        .values('student_id').annotate(avg=Avg('percentage'))
    }
    exam_avg_by_student = {
        row['student_id']: row['avg'] for row in
        StudentExamAttempt.objects.filter(student_id__in=student_ids, percentage__isnull=False)
        .values('student_id').annotate(avg=Avg('percentage'))
    }
    completion_by_student = {}
    for row in (
        LessonProgress.objects.filter(student_id__in=student_ids)
        .values('student_id').annotate(total=Count('id'), completed=Count('id', filter=Q(completed=True)))
    ):
        completion_by_student[row['student_id']] = (
            round(row['completed'] / row['total'] * 100, 2) if row['total'] else None
        )

    results = []
    for student in User.objects.filter(id__in=student_ids):
        results.append({
            'student_id': student.id,
            'username': student.username,
            'avg_quiz_percentage': quiz_avg_by_student.get(student.id),
            'avg_exam_percentage': exam_avg_by_student.get(student.id),
            'lesson_completion_rate': completion_by_student.get(student.id),
        })
    return api_response(data=results)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_quiz_performance(request):
    """Quiz performance: one row per quiz this teacher created."""
    quizzes = Quiz.objects.filter(created_by=request.user)
    results = []
    for quiz in quizzes:
        stats = StudentQuizAttempt.objects.filter(quiz=quiz, percentage__isnull=False).aggregate(
            attempt_count=Count('id'), avg_percentage=Avg('percentage'),
        )
        results.append({
            'quiz_id': quiz.id, 'title': quiz.title, 'status': quiz.status,
            'attempt_count': stats['attempt_count'], 'avg_percentage': stats['avg_percentage'],
        })
    return api_response(data=results)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_question_performance(request, quiz_id):
    """Question performance: one row per question in ONE of this
    teacher's own quizzes — which questions students most often miss."""
    try:
        quiz = Quiz.objects.get(id=quiz_id, created_by=request.user)
    except Quiz.DoesNotExist:
        raise ValidationError({'quiz_id': 'No such quiz.'})

    questions = quiz.questions.annotate(
        answered_count=Count('student_answers', filter=Q(student_answers__is_correct__isnull=False)),
        correct_count=Count('student_answers', filter=Q(student_answers__is_correct=True)),
    )
    results = []
    for question in questions:
        percent_correct = (
            round(question.correct_count / question.answered_count * 100, 2) if question.answered_count else None
        )
        results.append({
            'question_id': question.id, 'text': question.text, 'question_type': question.question_type,
            'answered_count': question.answered_count, 'correct_count': question.correct_count,
            'percent_correct': percent_correct,
        })
    return api_response(data=results)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_lesson_completion(request):
    """Lesson completion: one row per lesson this teacher created."""
    lessons = Lesson.objects.filter(created_by=request.user).annotate(
        started_count=Count('progress_records'),
        completed_count=Count('progress_records', filter=Q(progress_records__completed=True)),
    )
    results = []
    for lesson in lessons:
        completion_rate = (
            round(lesson.completed_count / lesson.started_count * 100, 2) if lesson.started_count else None
        )
        results.append({
            'lesson_id': lesson.id, 'title': lesson.title, 'status': lesson.status,
            'started_count': lesson.started_count, 'completed_count': lesson.completed_count,
            'completion_rate': completion_rate,
        })
    return api_response(data=results)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsTeacher])
def teacher_exam_performance(request):
    """Exam performance: one row per exam this teacher created."""

    exams = Exam.objects.filter(created_by=request.user).annotate(candidate_count=Count('candidates', distinct=True))
    results = []
    for exam in exams:
        avg_percentage = StudentExamAttempt.objects.filter(
            exam=exam, percentage__isnull=False,
        ).aggregate(avg=Avg('percentage'))['avg']
        results.append({
            'exam_id': exam.id, 'title': exam.title, 'status': exam.status,
            'candidate_count': exam.candidate_count, 'avg_percentage': avg_percentage,
        })
    return api_response(data=results)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSchoolAdmin])
def school_overview(request):
    """School-wide performance summary for the admin's own school."""
    school = request.user.school
    student_ids = User.objects.filter(school=school, role=User.Role.STUDENT).values_list('id', flat=True)

    return api_response(data={
        'school_id': school.id,
        'school_name': school.name,
        'student_count': User.objects.filter(school=school, role=User.Role.STUDENT).count(),
        'teacher_count': User.objects.filter(school=school, role=User.Role.TEACHER).count(),
        'class_count': SchoolClass.objects.filter(school=school).count(),
        'subject_count': Subject.objects.filter(school=school).count(),
        'avg_quiz_percentage': _avg_quiz_percentage_for_students(student_ids),
        'avg_exam_percentage': _avg_exam_percentage_for_students(student_ids),
        'lesson_completion_rate': _lesson_completion_rate_for_students(student_ids),
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSchoolAdmin])
def school_class_performance(request):
    """Class performance: one row per class in the admin's own school
    (broader than a single teacher's view — every class, any teacher)."""
    classes = SchoolClass.objects.filter(school=request.user.school)
    return api_response(data=[_class_summary(c) for c in classes])

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSchoolAdmin])
def school_subject_performance(request):
    """Subject performance: one row per subject in the admin's own school."""

    subjects = Subject.objects.filter(school=request.user.school)
    results = []
    for subject in subjects:
        quiz_avg = StudentQuizAttempt.objects.filter(
            quiz__subject=subject, percentage__isnull=False,
        ).aggregate(avg=Avg('percentage'))['avg']
        exam_avg = StudentExamAttempt.objects.filter(
            exam__subject=subject, percentage__isnull=False,
        ).aggregate(avg=Avg('percentage'))['avg']
        results.append({
            'subject_id': subject.id, 'name': subject.name,
            'avg_quiz_percentage': quiz_avg, 'avg_exam_percentage': exam_avg,
        })
    return api_response(data=results)

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSystemAdmin])
def system_schools(request):
    """One row per school in the system, with headcounts."""
    schools = School.objects.annotate(
        student_count=Count('users', filter=Q(users__role=User.Role.STUDENT), distinct=True),
        teacher_count=Count('users', filter=Q(users__role=User.Role.TEACHER), distinct=True),
        admin_count=Count('users', filter=Q(users__role=User.Role.ADMIN), distinct=True),
        class_count=Count('classes', distinct=True),
    )
    return api_response(data=[{
        'school_id': s.id, 'name': s.name, 'code': s.code, 'is_active': s.is_active,
        'student_count': s.student_count, 'teacher_count': s.teacher_count,
        'admin_count': s.admin_count, 'class_count': s.class_count,
    } for s in schools])

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSystemAdmin])
def system_users(request):
    """System-wide user counts by role and active status."""
    by_role = User.objects.values('role').annotate(count=Count('id')).order_by('role')
    return api_response(data={
        'total_users': User.objects.count(),
        'active_users': User.objects.filter(is_active=True).count(),
        'by_role': {row['role']: row['count'] for row in by_role},
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSystemAdmin])
def system_user_directory(request):
    """Read-only directory of school-owned accounts for system admins.

    System admin accounts are intentionally excluded from this directory;
    they are privileged platform operators, not school-managed accounts.
    """
    users = User.objects.select_related('school').exclude(
        role=User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM,
    ).order_by('school__name', 'role', 'last_name', 'first_name', 'username')
    return api_response(data=[{
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'phone_number': user.phone_number,
        'role': user.role,
        'admin_scope': user.admin_scope,
        'school': user.school_id,
        'school_name': user.school.name if user.school_id else '',
        'is_active': user.is_active,
        'date_joined': user.date_joined,
    } for user in users])

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsSystemAdmin])
def system_usage(request):
    """High-level system-wide activity counts."""
    return api_response(data={
        'school_count': School.objects.count(),
        'active_school_count': School.objects.filter(is_active=True).count(),
        'lesson_count': Lesson.objects.count(),
        'quiz_count': Quiz.objects.count(),
        'quiz_attempt_count': StudentQuizAttempt.objects.count(),
        'exam_count': Exam.objects.count(),
        'exam_candidate_count': ExamCandidate.objects.count(),
        'exam_script_count': ExamScript.objects.count(),
        'ai_explanation_request_count': AIExplanationRequest.objects.count(),
        'sync_record_count': SyncRecord.objects.count(),
    })
