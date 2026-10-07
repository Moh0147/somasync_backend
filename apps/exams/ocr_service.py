"""Best-effort document extraction. OCR is assistive and always reviewable."""
import json
import re


def extract_script(file_obj):
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError('OCR dependencies are missing. Install PyMuPDF, Pillow and pytesseract.') from exc
    file_obj.seek(0)
    data = file_obj.read()
    filetype = 'pdf' if data[:4] == b'%PDF' else ('png' if data[:8].startswith(b'\x89PNG') else 'jpg')
    document = fitz.open(stream=data, filetype=filetype)
    pages, ocr_used = [], False
    for page in document:
        text = page.get_text('text').strip()
        if len(text) < 20:
            try:
                import pytesseract
                from PIL import Image
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
                text, ocr_used = pytesseract.image_to_string(image).strip(), True
            except ImportError as exc:
                raise RuntimeError('Scanned PDF detected. Install Tesseract OCR plus Pillow and pytesseract.') from exc
        pages.append(text)
    return '\n\n'.join(p for p in pages if p), ('OCR' if ocr_used else 'TEXT')


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
        return result

    try:
        return _ai_map_answers(text, ordered)
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
- Include ALL question numbers as keys. Use an empty string "" if no answer is found.
- Preserve the student's full answer as written — do not summarise or correct it.
- The question numbers may appear in the script as "1.", "1)", "Q1", "Q1:", \
"Question 1", "i.", "a)" etc. Match them by position and context, not just literal text.
- Do not include any explanation outside the JSON object.

Example output for questions 1, 2, 3:
{{"1": "Photosynthesis is the process...", "2": "Carbon dioxide and water", "3": ""}}
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
