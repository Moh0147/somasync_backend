"""Best-effort document extraction. OCR is assistive and always reviewable."""
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
    answers = {}
    ordered = sorted(questions, key=lambda q: (q.order, q.id))
    for index, question in enumerate(ordered):
        number = re.escape(question.question_number)
        following = [re.escape(q.question_number) for q in ordered[index + 1:]]
        boundary = '|'.join(following) or r'$'
        pattern = rf'(?im)(?:^|\n)\s*(?:Q(?:uestion)?\s*)?{number}[.):\s]+(.*?)(?=(?:^|\n)\s*(?:Q(?:uestion)?\s*)?(?:{boundary})[.):\s]+|\Z)'
        match = re.search(pattern, text, re.S)
        if match:
            answers[question.id] = match.group(1).strip()
    return answers
