from rest_framework import serializers

from .models import (
    MatchingPair, Question, QuestionOption, Quiz, QuizAssignment, StudentQuizAnswer, StudentQuizAttempt,
)

class QuizSerializer(serializers.ModelSerializer):
    class Meta:
        model = Quiz
        fields = [
            'id', 'school', 'subject', 'class_level', 'topic', 'learning_objective',
            'title', 'description', 'status', 'created_by', 'published_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'status', 'created_by', 'published_at', 'created_at', 'updated_at']
        extra_kwargs = {'school': {'required': False}}
        validators = []

class QuestionOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionOption
        fields = ['id', 'text', 'is_correct', 'order']
        read_only_fields = ['id']

class MatchingPairSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchingPair
        fields = ['id', 'left_text', 'right_text', 'order']
        read_only_fields = ['id']

class QuestionSerializer(serializers.ModelSerializer):
    options = QuestionOptionSerializer(many=True, read_only=True)
    pairs = MatchingPairSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = [
            'id', 'school', 'quiz', 'question_type', 'text', 'marks', 'order',
            'explanation', 'created_by', 'options', 'pairs', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'school', 'created_by', 'options', 'pairs', 'created_at', 'updated_at']

class SafeQuestionOptionSerializer(serializers.ModelSerializer):
    """Same as QuestionOptionSerializer but omits `is_correct` — this is
    the one that's actually safe to hand to a student who hasn't answered
    yet. QuestionOptionSerializer/QuestionSerializer remain authoring-only
    (QuestionViewSet's own docstring already flags them as never meant to
    reach a student)."""

    class Meta:
        model = QuestionOption
        fields = ['id', 'text', 'order']

class SafeMatchingPairSerializer(serializers.ModelSerializer):
    """Left side only -- `right_text` is exactly the answer key for a
    MATCHING question (see QuizMarkingService._mark_matching_question),
    so it can never go to a student who hasn't answered yet."""

    class Meta:
        model = MatchingPair
        fields = ['id', 'left_text', 'order']

class SafeQuestionSerializer(serializers.ModelSerializer):
    """Student-facing: a question with its options/pairs but no answer
    key. Used by section mini-quizzes (Phase 23); nothing else in the base
    quiz system serves questions to a student before they've answered,
    since the live flow (Phase 8) only ever returns the attempt/answer
    rows, never the quiz's own question list.

    For MATCHING, `right_options` is the set of correct-side texts across
    every pair, shuffled -- the student picks from this pool per left
    item, without the pairing order itself leaking which goes with which.
    """

    options = SafeQuestionOptionSerializer(many=True, read_only=True)
    pairs = SafeMatchingPairSerializer(many=True, read_only=True)
    right_options = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = ['id', 'question_type', 'text', 'marks', 'order', 'options', 'pairs', 'right_options']

    def get_right_options(self, obj):
        if obj.question_type != Question.Type.MATCHING:
            return []
        import random
        texts = [pair.right_text for pair in obj.pairs.all()]
        random.shuffle(texts)
        return texts

class QuizAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuizAssignment
        fields = ['id', 'quiz', 'school_class', 'assigned_by', 'assigned_at']
        read_only_fields = fields

class AnswerInputSerializer(serializers.Serializer):
    """Shape of one answer event, used both by the single `answer` action
    and inside a `sync` batch."""

    client_answer_id = serializers.CharField(max_length=64)
    question = serializers.IntegerField()
    selected_option = serializers.IntegerField(required=False, allow_null=True)
    matching_response = serializers.DictField(required=False)

class StartAttemptSerializer(serializers.Serializer):
    quiz = serializers.IntegerField()
    client_attempt_id = serializers.CharField(max_length=64)

class SyncAttemptSerializer(serializers.Serializer):
    client_attempt_id = serializers.CharField(max_length=64)
    quiz = serializers.IntegerField()
    submitted = serializers.BooleanField(default=False)
    answers = AnswerInputSerializer(many=True, required=False)

class StudentQuizAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentQuizAnswer
        fields = [
            'id', 'question', 'client_answer_id', 'selected_option', 'matching_response',
            'is_correct', 'marks_awarded', 'answered_at',
        ]
        read_only_fields = fields

class StudentQuizAttemptSerializer(serializers.ModelSerializer):
    answers = StudentQuizAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = StudentQuizAttempt
        fields = [
            'id', 'school', 'quiz', 'student', 'client_attempt_id', 'status',
            'score', 'total_marks', 'percentage', 'started_at', 'submitted_at',
            'sync_error', 'answers', 'updated_at',
        ]
        read_only_fields = fields
