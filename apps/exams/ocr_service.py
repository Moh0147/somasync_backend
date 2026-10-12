"""Best-effort document extraction. OCR is assistive and always reviewable."""
import base64
import json
import os
import re


def extract_pages_as_png(file_obj) -> list[bytes]:
    """Render every page of a script file as a PNG byte string.

    PDF  → each page rendered at 150 DPI via PyMuPDF.
    Image (PNG/JPEG) → returned as-is in a single-element list (no re-encode).
    """
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('PyMuPDF is required for page extraction. Install it via pip.') from exc

    file_obj.seek(0)
    data = file_obj.read()

    # Single-image uploads: wrap the raw bytes directly.
    is_png  = data[:8].startswith(b'\x89PNG')
    is_jpeg = data[:2] == b'\xff\xd8'
    if is_png or is_jpeg:
        return [data]

    # PDF: render each page.
    document = fitz.open(stream=data, filetype='pdf')
    pages = []
    for page in document:
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
        pages.append(pix.tobytes('png'))
    return pages


_VISION_MAP_PROMPT = """\
This is an exam script page. The student has written answers to the questions below.

QUESTIONS (question_number: first words of the question):
{question_list}

Look carefully at the image and find the student's answer for each question number.
Return ONLY a valid JSON object mapping each question_number (string key) to the student's \
answer text (string value).
- Include ALL question numbers as keys.
- A single word like "Yes", "No", "Carbon", "A" IS a complete answer — include it.
- Use "" only if genuinely no answer is visible for that question.
- Do not include explanations outside the JSON.

Example: {{"1": "Photosynthesis is...", "2": "Carbon", "3": ""}}
"""


def vision_extract_answers(file_obj, questions) -> dict:
    """Combine OCR + answer mapping in one vision-model call per page.

    Sends each scanned page image directly to a vision model (GPT-4o or
    Ollama gemma3:4b) along with the question list and asks for a JSON
    mapping of question_number → answer text.  Skips the separate
    map_answers step entirely — avoids mis-attribution caused by the model
    producing question headers out of order.

    Returns {question.id: answer_str} on success, {} on failure.
    """
    try:
        import fitz
    except ImportError:
        return {}

    try:
        from django.conf import settings as _s
        openai_key = (getattr(_s, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')).strip()
    except Exception:
        openai_key = ''

    ordered = sorted(questions, key=lambda q: (q.order, q.id))
    if not ordered:
        return {}

    question_list = '\n'.join(
        f'- "{q.question_number}": {q.question_text[:100]}{"…" if len(q.question_text) > 100 else ""}'
        for q in ordered
    )
    prompt = _VISION_MAP_PROMPT.format(question_list=question_list)
    by_number = {q.question_number: q for q in ordered}

    file_obj.seek(0)
    data = file_obj.read()
    filetype = 'pdf' if data[:4] == b'%PDF' else ('png' if data[:8].startswith(b'\x89PNG') else 'jpg')
    document = fitz.open(stream=data, filetype=filetype)

    all_answers: dict = {}

    for page in document:
        rect = page.rect
        scale = 1.0 if (rect.width > 900 or rect.height > 900) else 2.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        png_bytes = pix.tobytes('png')

        raw_json = ''
        if openai_key:
            try:
                raw_json = _gpt4o_vision_map(png_bytes, prompt, openai_key)
            except Exception:
                pass

        if not raw_json:
            try:
                from apps.ai.services.ollama import OllamaAIService
                raw_json = OllamaAIService().generate_with_image(prompt, png_bytes)
            except Exception:
                pass

        if not raw_json:
            continue

        try:
            # Strip markdown code fences if present
            text = raw_json.strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text
                if text.rstrip().endswith('```'):
                    text = text.rstrip()[:-3].strip()
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                continue
            for qnum, answer in parsed.items():
                q = by_number.get(str(qnum))
                if not (q and isinstance(answer, str) and answer.strip()):
                    continue
                new_part = answer.strip()
                existing = all_answers.get(q.id, '')
                if existing:
                    # Answer spans multiple pages — concatenate page continuations.
                    # Skip if the new part is already contained in existing (avoid duplication).
                    if new_part not in existing:
                        all_answers[q.id] = existing + '\n' + new_part
                else:
                    all_answers[q.id] = new_part
        except (json.JSONDecodeError, Exception):
            pass

    return all_answers


def _gpt4o_vision_map(image_bytes: bytes, prompt: str, api_key: str) -> str:
    """Send image + prompt to GPT-4o and return raw text response."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    mime = 'image/jpeg' if image_bytes[:2] == b'\xff\xd8' else 'image/png'
    b64 = base64.b64encode(image_bytes).decode()
    response = client.chat.completions.create(
        model='gpt-4o',
        messages=[{
            'role': 'user',
            'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}', 'detail': 'high'}},
            ],
        }],
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()


def _gpt4o_ocr_page(image_bytes: bytes) -> str:
    """Send a page image to GPT-4o Vision and return the transcribed text."""
    from openai import OpenAI
    from django.conf import settings

    api_key = getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')
    client = OpenAI(api_key=api_key)

    mime = 'image/jpeg' if image_bytes[:2] == b'\xff\xd8' else 'image/png'
    b64 = base64.b64encode(image_bytes).decode()

    response = client.chat.completions.create(
        model='gpt-4o',
        messages=[{
            'role': 'user',
            'content': [
                {
                    'type': 'text',
                    'text': (
                        'Transcribe every word of text visible on this exam script page '
                        'exactly as written by the student. Preserve bullet points, '
                        'numbering, and line breaks. Do not summarise, correct, or add '
                        'any commentary — output the raw student text only.'
                    ),
                },
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:{mime};base64,{b64}', 'detail': 'high'},
                },
            ],
        }],
        max_tokens=4096,
    )
    return response.choices[0].message.content.strip()


def extract_script(file_obj):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('OCR dependencies are missing. Install PyMuPDF, Pillow and pytesseract.') from exc

    from django.conf import settings
    openai_key = (getattr(settings, 'OPENAI_API_KEY', '') or os.environ.get('OPENAI_API_KEY', '')).strip()
    use_gpt4o = bool(openai_key)

    file_obj.seek(0)
    data = file_obj.read()
    filetype = 'pdf' if data[:4] == b'%PDF' else ('png' if data[:8].startswith(b'\x89PNG') else 'jpg')
    document = fitz.open(stream=data, filetype=filetype)
    pages, ocr_used = [], False
    for page in document:
        text = page.get_text('text').strip()
        if len(text) >= 20:
            pages.append(text)
            continue

        # Scanned / camera-captured page — use GPT-4o Vision if key is set,
        # otherwise fall back to pytesseract.
        rect = page.rect
        is_camera_scan = rect.width > 900 or rect.height > 900
        scale = 1.0 if is_camera_scan else 2.0
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        png_bytes = pix.tobytes('png')

        if use_gpt4o:
            try:
                text = _gpt4o_ocr_page(png_bytes)
                ocr_used = True
            except Exception:
                # GPT-4o failed (no credits, network) — try Ollama vision next
                text = _ollama_ocr_page(png_bytes) or _pytesseract_ocr(pix)
                ocr_used = True
        else:
            # No GPT-4o key — try Ollama vision, then pytesseract
            text = _ollama_ocr_page(png_bytes) or _pytesseract_ocr(pix)
            ocr_used = True

        pages.append(text)
    return '\n\n'.join(p for p in pages if p), ('OCR' if ocr_used else 'TEXT')


def _ollama_ocr_page(image_bytes: bytes) -> str:
    """Use Ollama vision model (gemma3:4b) to transcribe a page image.
    Returns empty string on any failure so the caller can try pytesseract."""
    try:
        from apps.ai.services.ollama import OllamaAIService
        service = OllamaAIService()
        return service.generate_with_image(
            'Transcribe every word of text on this exam script page exactly as written. '
            'Preserve bullet points, numbering, and line breaks. Output the raw student text only.',
            image_bytes,
        )
    except Exception:
        return ''


def _pytesseract_ocr(pix) -> str:
    try:
        import pytesseract
        from PIL import Image
        image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(image).strip()
    except ImportError as exc:
        raise RuntimeError('Scanned PDF detected. Install Tesseract OCR plus Pillow and pytesseract.') from exc


def map_answers(text, questions):
    """Extract per-question answers from raw OCR text.

    Strategy (in order of preference):
    1. Header split   — when script uses explicit "Question N" or "Q N" headings
                        on their own line; splits deterministically on those
                        boundaries, immune to numbered lists inside answers.
    2. AI extraction  — fallback for non-standard layouts; uses a long
                        num_predict so full subjective answers are not cut off.
    3. Regex          — last resort; simple pattern match per question number.
    """
    ordered = sorted(questions, key=lambda q: (q.order, q.id))

    result = _header_map_answers(text, ordered)
    if result:
        # Supplement any gaps with regex before returning.
        missing = [q for q in ordered if q.id not in result]
        if missing:
            result.update(_regex_map_answers(text, missing))
        return result

    try:
        ai_result = _ai_map_answers(text, ordered)
        # AI may return empty strings for short or ambiguous answers.
        # Run regex on every question AI left unanswered so nothing is lost.
        missing = [q for q in ordered if q.id not in ai_result]
        if missing:
            ai_result.update(_regex_map_answers(text, missing))
        return ai_result
    except Exception:
        pass

    return _regex_map_answers(text, ordered)


# ── 1. Header-based split ──────────────────────────────────────────────────

# Matches a line that contains ONLY a question label such as:
#   "Question 1"  "QUESTION 3"  "Q 4"  "Q4"
# Captured group 1 = the numeric part.
_HEADER_RE = re.compile(
    r'(?im)^\s*(?:question|q)\s*(\d+)\s*$'
)


def _header_map_answers(text, ordered_questions):
    """Split the script on standalone question-header lines.

    Immune to numbered bullet points inside answers (e.g. "1. Education…"
    inside Q9) because those never appear as the sole content of a line
    that also carries a "Question" / "Q" prefix.
    Returns {} when no such headers are found."""
    parts = _HEADER_RE.split(text)
    # After split with one capturing group:
    #   parts = [pre_text, "1", body_of_Q1, "2", body_of_Q2, ...]
    if len(parts) < 3:
        return {}

    by_number = {q.question_number: q for q in ordered_questions}
    answers = {}
    for i in range(1, len(parts) - 1, 2):
        num = parts[i].strip()
        body = parts[i + 1].strip()
        q = by_number.get(num)
        if q and body:
            answers[q.id] = body
    return answers


# ── 2. AI extraction ───────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are processing an OCR-scanned exam script. Extract the student's answer for \
each question listed below.

FULL SCRIPT TEXT:
\"\"\"
{text}
\"\"\"

QUESTIONS TO EXTRACT (question_number: first words of question):
{question_list}

Instructions:
- Return ONLY a valid JSON object mapping each question_number (as a string key) \
to the student's answer text (as a string value).
- Include ALL question numbers as keys. Use an empty string "" ONLY if there is \
genuinely no answer at all for that question.
- A single word such as "Yes", "No", "True", "False", "Carbon", "A", "B" IS a \
complete valid answer — capture it exactly as written, never discard it.
- Preserve the student's full answer as written — do not summarise or correct it.
- The question numbers may appear in the script as "1.", "1)", "Q1", "Q1:", \
"Question 1", "i.", "a)" etc. Match them by position and context, not just literal text.
- Do not include any explanation outside the JSON object.

Example output for questions 1, 2, 3:
{{"1": "Photosynthesis is the process...", "2": "Carbon", "3": ""}}
"""


def _ai_map_answers(text, ordered_questions):
    from apps.ai.services import get_ai_service

    question_list = '\n'.join(
        f'- "{q.question_number}": {q.question_text[:120]}{"…" if len(q.question_text) > 120 else ""}'
        for q in ordered_questions
    )
    prompt = _EXTRACTION_PROMPT.format(text=text, question_list=question_list)
    result = get_ai_service().generate(prompt, num_predict=4000)
    raw = result.text.strip()

    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1] if '\n' in raw else raw
        if raw.rstrip().endswith('```'):
            raw = raw.rstrip()[:-3].strip()

    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError('AI did not return a JSON object')

    answers = {}
    for q in ordered_questions:
        value = parsed.get(str(q.question_number), '')
        if isinstance(value, str) and value.strip():
            answers[q.id] = value.strip()
    return answers


# ── 3. Regex fallback ──────────────────────────────────────────────────────

def _regex_map_answers(text, ordered_questions):
    """Last-resort regex. Uses [.):] only as separators (not whitespace) to
    avoid false matches on numbered list items inside answer bodies."""
    answers = {}
    for index, question in enumerate(ordered_questions):
        number = re.escape(question.question_number)
        following = [re.escape(q.question_number) for q in ordered_questions[index + 1:]]
        boundary = '|'.join(following) or r'$'
        pattern = (
            rf'(?im)(?:^|\n)\s*(?:Q(?:uestion)?\s*)?{number}[.):]\s*'
            rf'(.*?)'
            rf'(?=(?:^|\n)\s*(?:Q(?:uestion)?\s*)?(?:{boundary})[.):]\s|\Z)'
        )
        match = re.search(pattern, text, re.S)
        if match:
            answers[question.id] = match.group(1).strip()
    return answers
