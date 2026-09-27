import json

from apps.ai.exceptions import AIServiceError, AIServiceResponseError
from apps.ai.services import get_ai_service

PROMPT_TEMPLATE = """You are an experienced secondary-school examiner marking a student's written exam answer.

Question: {question_text}
Maximum marks: {max_marks}

Marking scheme (expected concepts and their mark allocation):
{marking_scheme}

Student's answer:
{student_answer}

Respond with STRICT JSON only, no other text, in exactly this shape:
{{"marks_awarded": <integer>, "feedback": "..."}}

Rules:
- marks_awarded must be a whole number between 0 and {max_marks}, inclusive.
- Award marks strictly according to the marking scheme criteria above -- do not invent extra criteria.
- feedback should be a short, specific explanation of what the student got right or missed, in plain language.
- Do not hallucinate facts not present in the marking scheme or the student's answer.
"""


def _build_prompt(exam_question, student_answer_text):
    scheme_lines = '\n'.join(
        f'- {item.criterion} ({item.marks} marks)' + (f': {item.description}' if item.description else '')
        for item in exam_question.marking_scheme_items.all()
    ) or '(no marking scheme items defined)'
    return PROMPT_TEMPLATE.format(
        question_text=exam_question.question_text,
        max_marks=exam_question.max_marks,
        marking_scheme=scheme_lines,
        student_answer=student_answer_text,
    )


def _extract_json_block(text):
    """Strips a markdown code fence if the model wrapped its JSON in one
    despite instructions not to — a common real-world LLM quirk (same
    defense used in Phase 13's explanation service)."""
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse_response(text, max_marks):
    try:
        data = json.loads(_extract_json_block(text))
    except (ValueError, TypeError) as exc:
        raise AIServiceResponseError('AI response was not valid JSON.') from exc
    if not isinstance(data, dict):
        raise AIServiceResponseError('AI response was not a JSON object.')

    marks_awarded = data.get('marks_awarded')
    feedback = data.get('feedback')

    if not isinstance(marks_awarded, (int, float)) or isinstance(marks_awarded, bool):
        raise AIServiceResponseError('AI response missing a valid numeric marks_awarded.')
    if not (0 <= marks_awarded <= max_marks):
        raise AIServiceResponseError(f'AI-awarded marks ({marks_awarded}) out of range 0-{max_marks}.')
    if not isinstance(feedback, str) or not feedback.strip():
        raise AIServiceResponseError('AI response missing feedback text.')

    return {'marks_awarded': marks_awarded, 'feedback': feedback.strip()}


class ExamAIMarkingService:
    """AI-assisted marking of SUBJECTIVE exam answers via the configured
    AIService (never Ollama directly — same abstraction as Phase 12/13).
    Populates ONLY ai_marks_awarded/ai_feedback; teacher_final_marks is
    NEVER touched here. AI must never directly finalize exam marks — a
    human always confirms via the separate teacher_final_marks field
    (enforced at the finalize step, ExamScriptViewSet.finalize)."""

    @classmethod
    def mark_answer(cls, answer):
        """Marks one subjective answer in place and saves it. Raises
        AIServiceError (unavailable backend, or a response that fails
        validation) rather than silently failing — the caller (script-
        level ai_mark action) decides how to record that as a failure."""
        question = answer.exam_question
        prompt = _build_prompt(question, answer.recorded_answer)
        result = get_ai_service().generate(prompt)
        parsed = _parse_response(result.text, question.max_marks)

        answer.ai_marks_awarded = parsed['marks_awarded']
        answer.ai_feedback = parsed['feedback']
        answer.save(update_fields=['ai_marks_awarded', 'ai_feedback'])
        return answer


__all__ = ['ExamAIMarkingService', 'AIServiceError']
