"""
สร้าง PDF รายงานสรุปการประชุม (สรุป + รายงานการมอบหมายงาน) จากผลสรุปที่มีอยู่ใน DB แล้ว

ใช้ฟอนต์ Tahoma (fonts/tahoma.ttf, fonts/tahomabd.ttf) เพราะรองรับภาษาไทยและมีมากับ
Windows อยู่แล้ว — ฟอนต์มาตรฐานของ PDF (Helvetica ฯลฯ) ไม่มีตัวอักษรไทย
"""

import pathlib

from fpdf import FPDF

HERE = pathlib.Path(__file__).parent
_FONT_REGULAR = HERE / "fonts" / "tahoma.ttf"
_FONT_BOLD = HERE / "fonts" / "tahomabd.ttf"


def _pdf() -> FPDF:
    pdf = FPDF()
    pdf.add_font("Tahoma", "", str(_FONT_REGULAR))
    pdf.add_font("Tahoma", "B", str(_FONT_BOLD))
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    return pdf


def build_summary_pdf(meeting: dict, summary: dict) -> bytes:
    """สร้าง PDF จาก meeting (db.get_meeting) + summary (db.get_summary) คืนค่าเป็น bytes"""
    pdf = _pdf()

    pdf.set_font("Tahoma", "B", 18)
    pdf.cell(0, 12, "รายงานสรุปการประชุม", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Tahoma", "", 11)
    meet_url = meeting.get("meet_url", "-")
    started_at = meeting.get("started_at")
    ended_at = meeting.get("ended_at")
    pdf.cell(0, 7, f"ลิงก์ประชุม: {meet_url}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(
        0, 7,
        f"เริ่ม: {started_at:%Y-%m-%d %H:%M}" if started_at else "เริ่ม: -",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.cell(
        0, 7,
        f"จบ: {ended_at:%Y-%m-%d %H:%M}" if ended_at else "จบ: -",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(4)

    pdf.set_font("Tahoma", "B", 13)
    pdf.cell(0, 9, "สรุปเนื้อหาการประชุม", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Tahoma", "", 11)
    pdf.multi_cell(0, 6.5, summary.get("executive_summary") or "-")
    pdf.ln(4)

    pdf.set_font("Tahoma", "B", 13)
    pdf.cell(0, 9, "รายงานการมอบหมายงาน", new_x="LMARGIN", new_y="NEXT")

    items = summary.get("action_items") or []
    if not items:
        pdf.set_font("Tahoma", "", 11)
        pdf.cell(0, 7, "— ไม่มี action item —", new_x="LMARGIN", new_y="NEXT")
    else:
        col_no, col_task, col_who, col_when = 10, 90, 45, 45
        row_h = 7

        def header_row():
            pdf.set_font("Tahoma", "B", 10)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(col_no, row_h, "ลำดับ", border=1, align="C", fill=True)
            pdf.cell(col_task, row_h, "วาระ / เรื่อง", border=1, fill=True)
            pdf.cell(col_who, row_h, "ผู้รับผิดชอบ", border=1, fill=True)
            pdf.cell(col_when, row_h, "กำหนด", border=1, new_x="LMARGIN", new_y="NEXT", fill=True)

        header_row()
        pdf.set_font("Tahoma", "", 10)
        for i, item in enumerate(items, start=1):
            # due_date มาจาก db.get_summary() เป็น datetime.date ดิบ ๆ (ไม่ได้ format เป็น string ให้
            # เหมือน due_time/due_time_end) ต้อง str() เองก่อนต่อ string ไม่งั้น += จะพังตอนมี due_time ด้วย
            when = str(item["due_date"]) if item.get("due_date") else "-"
            if item.get("due_time"):
                when += f" {item['due_time']}"
            assignee = item.get("assignee") or "-"
            description = item.get("description") or "-"

            # วัดความสูงที่ข้อความยาวที่สุดในแถวต้องใช้ ก่อนค่อยวาดทั้งแถวให้สูงเท่ากัน
            # (fpdf2 ไม่มี auto row-height ในตารางแบบ cell ต่อ cell)
            lines_task = pdf.multi_cell(col_task, row_h, description, align="L", dry_run=True, output="LINES")
            lines_who = pdf.multi_cell(col_who, row_h, assignee, align="L", dry_run=True, output="LINES")
            n_lines = max(len(lines_task), len(lines_who), 1)
            h = row_h * n_lines

            if pdf.get_y() + h > pdf.page_break_trigger:
                pdf.add_page()
                header_row()
                pdf.set_font("Tahoma", "", 10)

            x, y = pdf.get_x(), pdf.get_y()
            pdf.multi_cell(col_no, h, str(i), border=1, align="C")
            pdf.set_xy(x + col_no, y)
            pdf.multi_cell(col_task, row_h, description, border=1, align="L")
            pdf.set_xy(x + col_no + col_task, y)
            pdf.multi_cell(col_who, row_h, assignee, border=1, align="L")
            pdf.set_xy(x + col_no + col_task + col_who, y)
            pdf.multi_cell(col_when, h, when, border=1, align="C")
            pdf.set_xy(x, y + h)

    return bytes(pdf.output())
