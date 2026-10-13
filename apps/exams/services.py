from django.utils import timezone

from .models import ExamQuestion

class ExamMarkingService:

    @classmethod
    def mark_answer(cls, answer):

        question = answer.exam_question
        if question.question_type != ExamQuestion.Type.OBJECTIVE:
            return answer
        if not answer.recorded_answer:
            return answer

        is_correct = cls._answers_match(answer.recorded_answer, question.correct_answer)
        answer.is_correct = is_correct
        answer.teacher_final_marks = question.max_marks if is_correct else 0
        answer.marked_at = timezone.now()
        answer.save(update_fields=['is_correct', 'teacher_final_marks', 'marked_at'])
        return answer

    @classmethod
    def mark_all_objective(cls, attempt):
        """Marks every OBJECTIVE-question answer in `attempt` that has a
        recorded_answer. SUBJECTIVE answers are left untouched."""
        for answer in attempt.answers.select_related('exam_question'):
            if answer.exam_question.question_type == ExamQuestion.Type.OBJECTIVE:
                cls.mark_answer(answer)
        return attempt

    @staticmethod
    def _answers_match(recorded, correct):
        if not recorded or not correct:
            return False
        return recorded.strip().casefold() == correct.strip().casefold()
