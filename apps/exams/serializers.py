from rest_framework import serializers

from .models import (
    Exam, ExamCandidate, ExamQuestion, ExamScript, MarkingSchemeItem, StudentExamAnswer, StudentExamAttempt,
)


class ExamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exam
        fields = [
            'id', 'school', 'subject', 'class_level', 'title', 'description',
            'academic_year', 'exam_date', 'status', 'created_by', 'published_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'created_by', 'published_at', 'created_at', 'updated_at']
        extra_kwargs = {'school': {'required': False}}
        # See Phase 4/6/7 notes: the auto unique-together validator would
        # otherwise force `school` to be required, breaking the deferred
        # school-injection pattern. Checked manually in the view instead.
        validators = []


class MarkingSchemeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarkingSchemeItem
        fields = ['id', 'criterion', 'description', 'marks', 'order']
        read_only_fields = ['id']


class ExamQuestionSerializer(serializers.ModelSerializer):
    marking_scheme_items = MarkingSchemeItemSerializer(many=True, read_only=True)

    class Meta:
        model = ExamQuestion
        fields = [
            'id', 'school', 'exam', 'created_by', 'section', 'question_number', 'question_text',
            'question_type', 'max_marks', 'correct_answer', 'learning_objective', 'order',
            'marking_scheme_items', 'created_at', 'updated_at',
        ]
        # `school`/`created_by` are always derived from `exam` — never
        # independently choosable, same as quizzes.Question.
        read_only_fields = ['id', 'school', 'created_by', 'marking_scheme_items', 'created_at', 'updated_at']


class ExamCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamCandidate
        fields = ['id', 'exam', 'student', 'candidate_number', 'created_at']
        read_only_fields = fields


class ExamCandidateInputSerializer(serializers.Serializer):
    student = serializers.IntegerField()
    candidate_number = serializers.CharField(max_length=20)


class ExamScriptSerializer(serializers.ModelSerializer):
    file_reference = serializers.SerializerMethodField()

    class Meta:
        model = ExamScript
        fields = [
            'id', 'exam', 'candidate', 'student', 'file_reference', 'file_name', 'mime_type',
            'file_size', 'checksum', 'version', 'status', 'marking_error',
            'uploaded_by', 'uploaded_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_file_reference(self, obj):
        try:
            return obj.file.url
        except ValueError:
            return obj.file.name


class StudentExamAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentExamAnswer
        fields = [
            'id', 'attempt', 'exam_question', 'recorded_answer', 'is_correct',
            'ai_marks_awarded', 'ai_feedback', 'teacher_final_marks', 'teacher_comment',
            'marked_at', 'created_at',
        ]
        read_only_fields = fields


class MarkAnswerInputSerializer(serializers.Serializer):
    """For an OBJECTIVE question: send `recorded_answer` and the answer is
    marked deterministically (ExamMarkingService), no marks needed. For a
    SUBJECTIVE question (or to override an objective one): send
    `teacher_final_marks` directly — this is the manual-marking fallback
    Phase 17's AI assistance doesn't exist to replace yet."""

    recorded_answer = serializers.CharField(required=False, allow_blank=True)
    teacher_final_marks = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, min_value=0)
    teacher_comment = serializers.CharField(required=False, allow_blank=True)


class ExamResultAnswerSerializer(serializers.ModelSerializer):
    """Student-facing: only the confirmed mark and the teacher's own
    comment — never ai_marks_awarded/ai_feedback (an internal suggestion,
    not necessarily what the teacher agreed with) or recorded_answer
    (nothing useful to re-show the student)."""

    question_number = serializers.CharField(source='exam_question.question_number', read_only=True)
    question_text = serializers.CharField(source='exam_question.question_text', read_only=True)
    max_marks = serializers.IntegerField(source='exam_question.max_marks', read_only=True)

    class Meta:
        model = StudentExamAnswer
        fields = ['id', 'question_number', 'question_text', 'max_marks', 'teacher_final_marks', 'teacher_comment']
        read_only_fields = fields


class ExamResultSerializer(serializers.ModelSerializer):
    exam_title = serializers.CharField(source='exam.title', read_only=True)
    candidate_number = serializers.CharField(source='candidate.candidate_number', read_only=True)
    answers = ExamResultAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = StudentExamAttempt
        fields = [
            'id', 'exam', 'exam_title', 'candidate_number', 'score', 'total_marks', 'percentage',
            'answers', 'updated_at',
        ]
        read_only_fields = fields


class StudentExamAttemptSerializer(serializers.ModelSerializer):
    answers = StudentExamAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = StudentExamAttempt
        fields = [
            'id', 'school', 'exam', 'candidate', 'student', 'current_script',
            'score', 'total_marks', 'percentage', 'answers', 'created_at', 'updated_at',
        ]
        read_only_fields = fields
