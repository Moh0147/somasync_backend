import hashlib
import json

from rest_framework.exceptions import ValidationError

from apps.quizzes.models import Question, StudentQuizAnswer

from .exceptions import AIServiceError, AIServiceResponseError
from .models import AIExplanationRequest, MistakeExplanationCache
from .services import get_ai_service

PROMPT_TEMPLATE = """You are a patient secondary-school tutor helping a student understand a mistake on a quiz.

Subject: {subject}
Topic: {topic}
Learning objective: {learning_objective}

Question: {question}
Correct answer: {correct_answer}
The student's answer: {student_answer}
{context_block}
Respond with STRICT JSON only, no other text, in exactly this shape:
{{"explanation": "...", "correct_concept": "...", "concise_feedback": "..."}}

Rules:
- explanation: explain SPECIFICALLY why the student's answer is wrong, in simple language a secondary school student can understand.
- correct_concept: explain the correct concept/rule clearly and concisely.
- concise_feedback: one short, encouraging sentence.
- Stay strictly within the question/subject/topic given above. Do not introduce unrelated facts.
- If you are not certain of something, say so plainly rather than inventing a fact.
- Keep the whole response concise -- a student will read this on a phone.
"""


def _wrong_answer_signature(answer):
    if answer.selected_option_id:
        return f'option:{answer.selected_option_id}'
    payload = json.dumps(answer.matching_response or {}, sort_keys=True)
    return f'matching:{hashlib.sha256(payload.encode()).hexdigest()[:16]}'


def _correct_answer_text(question):
    if question.question_type == Question.Type.MATCHING:
        return '; '.join(f'{p.left_text} -> {p.right_text}' for p in question.pairs.all())
    correct_option = question.options.filter(is_correct=True).first()
    return correct_option.text if correct_option else ''


def _student_answer_text(question, answer):
    if question.question_type == Question.Type.MATCHING:
        left_by_pair = {str(p.id): p.left_text for p in question.pairs.all()}
        parts = [f'{left_by_pair.get(k, k)} -> {v}' for k, v in (answer.matching_response or {}).items()]
        return '; '.join(parts)
    return answer.selected_option.text if answer.selected_option else '(no answer given)'


def _build_prompt(question, correct_answer_text, student_answer_text, context):
    quiz = question.quiz
    return PROMPT_TEMPLATE.format(
        subject=quiz.subject.name,
        topic=quiz.topic.title if quiz.topic else 'N/A',
        learning_objective=quiz.learning_objective.description if quiz.learning_objective else 'N/A',
        question=question.text,
        correct_answer=correct_answer_text,
        student_answer=student_answer_text,
        context_block=f'Additional context from the student: {context}\n' if context else '',
    )


def _extract_json_block(text):
    """Strips a markdown code fence if the model wrapped its JSON in one
    despite instructions not to — a common real-world LLM quirk."""
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3]
    return text.strip()


def _parse_ai_response(text):
    try:
        data = json.loads(_extract_json_block(text))
    except (ValueError, TypeError) as exc:
        raise AIServiceResponseError('AI response was not valid JSON.') from exc

    if not isinstance(data, dict):
        raise AIServiceResponseError('AI response was not a JSON object.')

    required = ('explanation', 'correct_concept', 'concise_feedback')
    missing = [key for key in required if not isinstance(data.get(key), str) or not data.get(key).strip()]
    if missing:
        raise AIServiceResponseError(f'AI response missing required field(s): {", ".join(missing)}.')

    return {key: data[key].strip() for key in required}


def request_explanation(user, student_answer_id, context=''):
    """Creates an AIExplanationRequest for a student's own wrong answer —
    served instantly from MistakeExplanationCache if another student
    already triggered the same (question, wrong answer) explanation,
    otherwise generated fresh via the configured AIService.

    Raises ValidationError (no row created) for ownership/eligibility
    issues — there's nothing valid to persist. Once the answer is
    confirmed valid and owned by `user`, this always returns the request
    row (never raises) — an AI failure marks it FAILED with `error` set,
    exactly like Phase 8/11's sync failure handling, so the caller can
    still see and retry it.
    """
    try:
        answer = StudentQuizAnswer.objects.select_related(
            'attempt', 'question__quiz__subject', 'question__quiz__topic',
            'question__quiz__learning_objective', 'selected_option',
        ).get(pk=student_answer_id)
    except StudentQuizAnswer.DoesNotExist:
        raise ValidationError({'student_answer': 'No such answer.'})

    # Same generic message whether it doesn't exist or belongs to someone
    # else -- never confirm another student's answer exists.
    if answer.attempt.student_id != user.id:
        raise ValidationError({'student_answer': 'No such answer.'})
    if answer.is_correct is None:
        raise ValidationError({'student_answer': 'This answer has not been marked yet.'})
    if answer.is_correct:
        raise ValidationError({'student_answer': 'This answer was correct — nothing to explain.'})

    question = answer.question
    signature = _wrong_answer_signature(answer)

    request_obj = AIExplanationRequest.objects.create(
        school=answer.attempt.school, student=user, student_answer=answer,
        context=context, status=AIExplanationRequest.Status.PENDING,
    )

    cached = MistakeExplanationCache.objects.filter(question=question, wrong_answer_signature=signature).first()
    if cached:
        cached.hit_count += 1
        cached.save(update_fields=['hit_count'])
        request_obj.status = AIExplanationRequest.Status.COMPLETED
        request_obj.explanation = cached.explanation
        request_obj.correct_concept = cached.correct_concept
        request_obj.concise_feedback = cached.concise_feedback
        request_obj.save(update_fields=['status', 'explanation', 'correct_concept', 'concise_feedback', 'updated_at'])
        return request_obj

    return generate_and_store(request_obj)


def generate_and_store(request_obj):
    """Runs (or re-runs, for a retry) generation for an existing request
    row, updating it in place. Never raises — failures are recorded as
    status=FAILED with `error` set."""
    answer = request_obj.student_answer
    question = answer.question
    signature = _wrong_answer_signature(answer)

    request_obj.status = AIExplanationRequest.Status.PROCESSING
    request_obj.attempts += 1
    request_obj.save(update_fields=['status', 'attempts', 'updated_at'])

    prompt = _build_prompt(
        question, _correct_answer_text(question), _student_answer_text(question, answer), request_obj.context,
    )

    try:
        result = get_ai_service().generate(prompt)
        parsed = _parse_ai_response(result.text)
    except AIServiceError as exc:
        request_obj.status = AIExplanationRequest.Status.FAILED
        request_obj.error = str(exc)
        request_obj.save(update_fields=['status', 'error', 'updated_at'])
        return request_obj

    request_obj.status = AIExplanationRequest.Status.COMPLETED
    request_obj.explanation = parsed['explanation']
    request_obj.correct_concept = parsed['correct_concept']
    request_obj.concise_feedback = parsed['concise_feedback']
    request_obj.error = ''
    request_obj.save(update_fields=[
        'status', 'explanation', 'correct_concept', 'concise_feedback', 'error', 'updated_at',
    ])

    MistakeExplanationCache.objects.update_or_create(
        question=question, wrong_answer_signature=signature,
        defaults={
            'explanation': parsed['explanation'],
            'correct_concept': parsed['correct_concept'],
            'concise_feedback': parsed['concise_feedback'],
        },
    )
    return request_obj
