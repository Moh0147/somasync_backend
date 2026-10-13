import io

import fitz
import qrcode
from PIL import Image

PAGE_W = 595
PAGE_H = 842

def _qr_png_bytes(data: str) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img: Image.Image = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def _ct(page: fitz.Page, y: float, text: str, fontsize: float,
        fontname: str = 'helv', color: tuple = (0, 0, 0),
        x0: float = 40, x1: float = PAGE_W - 40) -> None:
    """Insert horizontally-centered text within [x0, x1] at baseline y."""
    text_w = fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
    x = x0 + max(0.0, (x1 - x0 - text_w) / 2)
    page.insert_text((x, y), text, fontsize=fontsize, fontname=fontname, color=color)

def generate_cover_pages(exam, candidates) -> bytes:

    doc = fitz.open()

    school_name = exam.school.name
    school_code = exam.school.code
    exam_title = exam.title
    subject_name = exam.subject.name
    level_name = exam.class_level.name
    exam_date = str(exam.exam_date) if exam.exam_date else '—'
    academic_year = str(exam.academic_year)
    total_marks = str(exam.total_marks) if exam.total_marks else '—'

    for candidate in candidates:
        page: fitz.Page = doc.new_page(width=PAGE_W, height=PAGE_H)

        page.draw_rect(fitz.Rect(0, 0, PAGE_W, 6), color=None, fill=(0.1, 0.3, 0.7))

        _ct(page, 40, school_name.upper(), 14, color=(0.1, 0.3, 0.7))
        _ct(page, 58, f'({school_code})', 10, color=(0.4, 0.4, 0.4))

        page.draw_line((40, 68), (PAGE_W - 40, 68), color=(0.8, 0.8, 0.8), width=0.5)

        _ct(page, 90, exam_title, 16, color=(0.05, 0.05, 0.05))

        meta = f'{subject_name}  ·  {level_name}  ·  {academic_year}'
        _ct(page, 110, meta, 10, color=(0.4, 0.4, 0.4))

        date_marks = f'Date: {exam_date}   Total Marks: {total_marks}'
        _ct(page, 126, date_marks, 9, color=(0.5, 0.5, 0.5))

        page.draw_line(
            (40, 144), (PAGE_W - 40, 144),
            color=(0.6, 0.6, 0.6), width=0.5,
            dashes='[4 4]',
        )
        _ct(page, 156, '— CANDIDATE COVER PAGE — attach to front of answer script —',
            7, color=(0.6, 0.6, 0.6))

        qr_size = 160
        qr_x = 60
        qr_y = 200
        qr_png = _qr_png_bytes(candidate.candidate_number)
        page.insert_image(
            fitz.Rect(qr_x, qr_y, qr_x + qr_size, qr_y + qr_size),
            stream=qr_png,
        )
        _ct(page, qr_y + qr_size + 14, 'Scan to identify candidate',
            8, color=(0.5, 0.5, 0.5), x0=qr_x, x1=qr_x + qr_size)

        num_x = qr_x + qr_size + 40
        num_w = PAGE_W - num_x - 40

        _ct(page, 240, 'CANDIDATE NO.', 11, color=(0.4, 0.4, 0.4),
            x0=num_x, x1=num_x + num_w)

        _ct(page, 300, candidate.candidate_number, 36, fontname='hebo',
            color=(0.05, 0.05, 0.05), x0=num_x, x1=num_x + num_w)

        student = candidate.student
        display_name = student.get_full_name() or student.username
        _ct(page, 330, display_name, 13, color=(0.2, 0.2, 0.2),
            x0=num_x, x1=num_x + num_w)

        box_y = qr_y + qr_size + 50
        page.draw_rect(
            fitz.Rect(40, box_y, PAGE_W - 40, box_y + 90),
            color=(0.85, 0.9, 1.0),
            fill=(0.95, 0.97, 1.0),
            width=0.8,
        )
        instructions = [
            'Instructions for teacher:',
            '1. Attach this page to the FRONT of the student\'s answer script.',
            '2. During script upload, point the camera at the QR code on this page.',
            '3. The system will automatically identify the candidate.',
            '4. Then photograph all remaining pages of the script.',
        ]
        for i, line in enumerate(instructions):
            page.insert_text(
                (56, box_y + 16 + i * 15),
                line,
                fontsize=9,
                fontname='hebo' if i == 0 else 'helv',
                color=(0.1, 0.2, 0.5),
            )

        page.draw_rect(fitz.Rect(0, PAGE_H - 6, PAGE_W, PAGE_H), color=None, fill=(0.1, 0.3, 0.7))

    buf = io.BytesIO()
    doc.save(buf, deflate=False, garbage=3, clean=True)
    doc.close()
    return buf.getvalue()
