"""
Generates a printable mark sheet PDF for a completed exam.

Layout: A3 landscape (or A4 landscape when few questions).
  - One row per candidate (sorted by candidate_number).
  - One column per question (Q1 … Qn), plus Total and %.
  - Footer: count, mean, highest, lowest.

Dependencies already in requirements.txt: PyMuPDF (fitz).
"""

import io
from decimal import Decimal

import fitz

PAGE_W = 842
PAGE_H = 595

MARGIN = 30
COL_CAND = 90
COL_NAME = 110
COL_Q = 36
COL_TOT = 50
COL_PCT = 44

ROW_H = 18
HEADER_H = 48

def _text(page: fitz.Page, rect: fitz.Rect, text: str, fontsize: float = 8,
          fontname: str = 'helv', color=(0, 0, 0), align: int = 1) -> None:
    """Insert text centred (align=1) or left-aligned (align=0) in rect."""
    tw = fitz.get_text_length(str(text), fontname=fontname, fontsize=fontsize)
    x_off = max(0.0, (rect.width - tw) / 2) if align == 1 else 4
    page.insert_text(
        (rect.x0 + x_off, rect.y0 + (rect.height + fontsize) / 2 - 1),
        str(text),
        fontsize=fontsize,
        fontname=fontname,
        color=color,
    )

def _cell(page: fitz.Page, rect: fitz.Rect, text: str,
          fontsize: float = 8, fontname: str = 'helv',
          fill=None, color=(0, 0, 0), align: int = 1,
          border_color=(0.75, 0.75, 0.75)) -> None:
    if fill:
        page.draw_rect(rect, color=None, fill=fill)
    page.draw_rect(rect, color=border_color, width=0.3)
    _text(page, rect, text, fontsize=fontsize, fontname=fontname, color=color, align=align)

def generate_mark_sheet(exam, questions, candidates_with_attempts) -> bytes:
    """Return a PDF (bytes) — one page per ~35 candidates.

    Parameters
    ----------
    exam:
        Exam instance.
    questions:
        Ordered list of ExamQuestion instances (already prefetched).
    candidates_with_attempts:
        List of dicts: {
            'candidate_number': str,
            'student_name': str,
            'marks': {question_id: Decimal | None},  # teacher_final_marks
            'total': Decimal | None,
            'percentage': Decimal | None,
        }
    """
    doc = fitz.open()

    n_questions = len(questions)
    extra_q_cols = max(0, n_questions - 12)
    page_w = PAGE_W + extra_q_cols * COL_Q
    page_h = PAGE_H

    def _draw_page_header(page: fitz.Page) -> float:
        """Draw school/exam header and return y where the table starts."""
        page.draw_rect(fitz.Rect(0, 0, page_w, 5), color=None, fill=(0.1, 0.3, 0.7))

        school_name = exam.school.name.upper()
        exam_title = exam.title
        subject = exam.subject.name
        level = exam.class_level.name
        year = str(exam.academic_year)
        date = str(exam.exam_date) if exam.exam_date else '—'

        tw = fitz.get_text_length(school_name, fontname='hebo', fontsize=11)
        page.insert_text((page_w / 2 - tw / 2, 22), school_name,
                         fontsize=11, fontname='hebo', color=(0.1, 0.3, 0.7))

        meta = f'{exam_title}  ·  {subject}  ·  {level}  ·  {year}  ·  {date}'
        tw2 = fitz.get_text_length(meta, fontname='helv', fontsize=8)
        page.insert_text((page_w / 2 - tw2 / 2, 36), meta,
                         fontsize=8, fontname='helv', color=(0.35, 0.35, 0.35))

        label = 'MARK SHEET'
        tw3 = fitz.get_text_length(label, fontname='hebo', fontsize=8)
        page.insert_text((page_w / 2 - tw3 / 2, 48), label,
                         fontsize=8, fontname='hebo', color=(0.5, 0.5, 0.5))

        page.draw_line((MARGIN, 55), (page_w - MARGIN, 55),
                       color=(0.8, 0.8, 0.8), width=0.5)
        return 62.0

    def _draw_column_headers(page: fitz.Page, y: float) -> float:
        x = MARGIN
        hdr_bg = (0.1, 0.3, 0.7)
        hdr_fg = (1, 1, 1)

        _cell(page, fitz.Rect(x, y, x + COL_CAND, y + ROW_H),
              'Cand. No.', fontname='hebo', fontsize=7, fill=hdr_bg, color=hdr_fg)
        x += COL_CAND

        _cell(page, fitz.Rect(x, y, x + COL_NAME, y + ROW_H),
              'Name', fontname='hebo', fontsize=7, fill=hdr_bg, color=hdr_fg, align=0)
        x += COL_NAME

        for q in questions:
            label = f'Q{q.question_number}'
            sub = f'/{float(q.max_marks):g}'
            r = fitz.Rect(x, y, x + COL_Q, y + ROW_H)
            page.draw_rect(r, color=None, fill=hdr_bg)
            page.draw_rect(r, color=(0.75, 0.75, 0.75), width=0.3)
            page.insert_text((x + 2, y + 10), label, fontsize=6, fontname='hebo', color=hdr_fg)
            page.insert_text((x + 2, y + 16), sub, fontsize=5.5, fontname='helv', color=(0.8, 0.9, 1.0))
            x += COL_Q

        _cell(page, fitz.Rect(x, y, x + COL_TOT, y + ROW_H),
              'Total', fontname='hebo', fontsize=7, fill=hdr_bg, color=hdr_fg)
        x += COL_TOT

        _cell(page, fitz.Rect(x, y, x + COL_PCT, y + ROW_H),
              '%', fontname='hebo', fontsize=7, fill=hdr_bg, color=hdr_fg)

        return y + ROW_H

    rows_per_page = int((page_h - 62 - ROW_H - MARGIN - 40) / ROW_H)
    chunks = [
        candidates_with_attempts[i:i + rows_per_page]
        for i in range(0, max(1, len(candidates_with_attempts)), rows_per_page)
    ]

    totals_collected = [
        row['total'] for row in candidates_with_attempts if row['total'] is not None
    ]

    for chunk in chunks:
        page: fitz.Page = doc.new_page(width=page_w, height=page_h)
        y = _draw_page_header(page)
        y = _draw_column_headers(page, y)

        for row_idx, row in enumerate(chunk):
            bg = (0.97, 0.97, 1.0) if row_idx % 2 == 0 else (1, 1, 1)
            x = MARGIN

            _cell(page, fitz.Rect(x, y, x + COL_CAND, y + ROW_H),
                  row['candidate_number'], fontsize=7, fill=bg)
            x += COL_CAND

            _cell(page, fitz.Rect(x, y, x + COL_NAME, y + ROW_H),
                  row['student_name'][:22], fontsize=7, fill=bg, align=0)
            x += COL_NAME

            for q in questions:
                marks = row['marks'].get(q.id)
                mark_str = f'{float(marks):g}' if marks is not None else '—'
                _cell(page, fitz.Rect(x, y, x + COL_Q, y + ROW_H),
                      mark_str, fontsize=7, fill=bg)
                x += COL_Q

            total_str = f'{float(row["total"]):g}' if row['total'] is not None else '—'
            _cell(page, fitz.Rect(x, y, x + COL_TOT, y + ROW_H),
                  total_str, fontsize=7,
                  fill=bg, fontname='hebo', color=(0.05, 0.05, 0.4))
            x += COL_TOT

            pct_str = f'{float(row["percentage"]):.1f}%' if row['percentage'] is not None else '—'
            pct_val = float(row['percentage']) if row['percentage'] is not None else -1
            pct_color = (0.0, 0.45, 0.1) if pct_val >= 40 else (0.7, 0.0, 0.0)
            _cell(page, fitz.Rect(x, y, x + COL_PCT, y + ROW_H),
                  pct_str, fontsize=7, fill=bg, color=pct_color)

            y += ROW_H

        y += 6
        n = len(totals_collected)
        if n:
            mean_val = sum(float(t) for t in totals_collected) / n
            high_val = max(float(t) for t in totals_collected)
            low_val  = min(float(t) for t in totals_collected)
            footer = (
                f'Candidates: {len(candidates_with_attempts)}    '
                f'Marked: {n}    '
                f'Mean: {mean_val:.1f}    '
                f'Highest: {high_val:g}    '
                f'Lowest: {low_val:g}'
            )
        else:
            footer = f'Candidates: {len(candidates_with_attempts)}    No marks recorded yet.'

        page.insert_text((MARGIN, y + 10), footer,
                         fontsize=7.5, fontname='helv', color=(0.35, 0.35, 0.35))

        page.draw_rect(fitz.Rect(0, page_h - 5, page_w, page_h),
                       color=None, fill=(0.1, 0.3, 0.7))

    buf = io.BytesIO()
    doc.save(buf, deflate=False, garbage=3, clean=True)
    doc.close()
    return buf.getvalue()
