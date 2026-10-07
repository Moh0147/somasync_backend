import json
import re

from apps.ai.exceptions import AIServiceError, AIServiceResponseError
from apps.ai.services import get_ai_service

# Matches a marking scheme criterion that encodes a sub-answer, e.g.:
#   "(i) B — Problem identification"   → key="i"  expected="B"
#   "(vii) C"                           → key="vii" expected="C"
#   "3. D"                              → key="3"  expected="D"
_SCHEME_ITEM_RE = re.compile(
    r'^\s*[\(\[]?\s*([ivxlcdmIVXLCDM]+|\d+|[a-zA-Z])\s*[\)\]]?\s*[.:\-]?\s*([A-Fa-f])\b',
)
_STUDENT_SUB_RE = re.compile(
    r'[\(\[]?\s*([ivxlcdmIVXLCDM]+|\d+|[a-zA-Z])\s*[\)\]]?\s*[.:\-]?\s*([A-Fa-f])\b',
)


def _try_deterministic_mark(exam_question, student_answer_text):
    """Return (marks, feedback) when every marking-scheme criterion encodes
    an MCQ/matching sub-answer (e.g. "(i) B — label").  Returns None when
    the scheme items are descriptive prose, leaving it to the AI path."""
    items = list(exam_question.marking_scheme_items.order_by('order', 'id'))
    if not items:
        return None

    scheme = {}
    for item in items:
        m = _SCHEME_ITEM_RE.match(item.criterion)
        if not m:
            return None  # at least one item is descriptive — use AI
        key = m.group(1).lower()
        scheme[key] = (m.group(2).upper(), float(item.marks))

    # Parse sub-answers from the student's response
    student = {m.group(1).lower(): m.group(2).upper() for m in _STUDENT_SUB_RE.finditer(student_answer_text)}

    earned = 0.0
    correct_parts, wrong_parts, missing_parts = [], [], []
    for key, (expected, item_marks) in scheme.items():
        got = student.get(key)
        if got is None:
            missing_parts.append(f'({key})')
        elif got == expected:
            earned += item_marks
            correct_parts.append(f'({key}) {got} ✓')
        else:
            wrong_parts.append(f'({key}) wrote {got}, expected {expected}')

    parts = []
    if correct_parts:
        parts.append('Correct: ' + ', '.join(correct_parts))
    if wrong_parts:
        parts.append('Wrong: ' + ', '.join(wrong_parts))
    if missing_parts:
        parts.append('Not answered: ' + ', '.join(missing_parts))

    return earned, '. '.join(parts) or 'All answers marked.'

PROMPT_TEMPLATE = """You are an experienced secondary-school examiner marking a student's written exam answer.

Subject: {subject}
Topic: {topic}
Class level: {class_level}

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
- Award a mark for a criterion when the student expresses the SAME IDEA or CONCEPT, even if worded differently, paraphrased, or using synonyms. Do NOT require the exact wording of the marking scheme.
- Award marks strictly according to the marking scheme criteria above — do not invent extra criteria.
- Ignore any introductory or opening sentence that merely restates the question (e.g. "Cardiac muscles are adapted in the following ways:"). Only the specific points that follow earn marks.
- feedback should be a short, specific explanation of what the student got right or missed, in plain language.
- Do not hallucinate facts not present in the marking scheme or the student's answer.
"""


def _build_prompt(exam_question, student_answer_text):
    scheme_lines = '\n'.join(
        f'- {item.criterion} ({item.marks} marks)' + (f': {item.description}' if item.description else '')
        for item in exam_question.marking_scheme_items.all()
    ) or '(no marking scheme items defined)'

    exam = exam_question.exam
    subject = exam.subject.name if hasattr(exam.subject, 'name') else str(exam.subject)
    class_level = exam.class_level.name if hasattr(exam.class_level, 'name') else str(exam.class_level)
    obj = exam_question.learning_objective
    topic = obj.topic.name if obj and hasattr(obj, 'topic') and hasattr(obj.topic, 'name') else 'General'

    return PROMPT_TEMPLATE.format(
        subject=subject,
        topic=topic,
        class_level=class_level,
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
        """Marks one subjective answer in place and saves it.

        If the marking scheme items encode MCQ/matching sub-answers
        (e.g. "(i) B — label"), marks deterministically from the scheme
        so the backend is always the source of truth — AI is never asked
        to judge what the correct letter should be.

        Falls through to AI only for descriptive/open-ended scheme items.

        Raises AIServiceError on AI backend failures."""
        question = answer.exam_question

        det = _try_deterministic_mark(question, answer.recorded_answer)
        if det is not None:
            marks, feedback = det
            answer.ai_marks_awarded = marks
            answer.ai_feedback = feedback
            answer.save(update_fields=['ai_marks_awarded', 'ai_feedback'])
            return answer

        prompt = _build_prompt(question, answer.recorded_answer)
        result = get_ai_service().generate(prompt)
        parsed = _parse_response(result.text, question.max_marks)

        answer.ai_marks_awarded = parsed['marks_awarded']
        answer.ai_feedback = parsed['feedback']
        answer.save(update_fields=['ai_marks_awarded', 'ai_feedback'])
        return answer


__all__ = ['ExamAIMarkingService', 'AIServiceError']
