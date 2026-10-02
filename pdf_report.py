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


# ── รายงานการประชุมตามแบบฟอร์ม (ใช้ report_template.TEMPLATE + report_data.build_header) ──

class _ReportPDF(FPDF):
    def __init__(self, draft: bool, watermark: str):
        super().__init__()
        self.draft = draft
        self.watermark = watermark
        self.add_font("Tahoma", "", str(_FONT_REGULAR))
        self.add_font("Tahoma", "B", str(_FONT_BOLD))
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(18, 15, 18)
        self.alias_nb_pages()

    def header(self):
        if self.draft:   # ลายน้ำวาดก่อนเนื้อหา เนื้อหาจึงทับอยู่ด้านบน
            with self.rotation(45, x=105, y=150):
                self.set_font("Tahoma", "B", 80)
                self.set_text_color(225, 225, 225)
                self.text(48, 165, self.watermark)
            self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-13)
        self.set_font("Tahoma", "", 9)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, f"หน้า {self.page_no()}/{{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def _line(pdf: FPDF, text: str, size: float = 12, bold: bool = False, indent: float = 0, h: float = 7.5):
    pdf.set_font("Tahoma", "B" if bold else "", size)
    pdf.set_x(pdf.l_margin + indent)
    # align="L": ค่าเริ่มต้นของ multi_cell คือจัดชิดสองขอบ ซึ่งกับภาษาไทย (วรรคน้อย) ทำให้ช่องว่างถ่างผิดปกติ
    pdf.multi_cell(0, h, text, align="L", new_x="LMARGIN", new_y="NEXT")


def _fmt_due(item: dict) -> str:
    when = str(item["due_date"]) if item.get("due_date") else "-"
    if item.get("due_time"):
        when += f" {item['due_time']}"
        if item.get("due_time_end"):
            when += f"-{item['due_time_end']}"
    return when


def build_minutes_pdf(
    meeting: dict,
    participants: list[dict],
    content: dict,
    *,
    approved: bool = False,
    approved_by: str | None = None,
    approved_at=None,
) -> bytes:
    """สร้าง PDF รายงานการประชุมตามแบบฟอร์ม — ยังไม่อนุมัติจะมีลายน้ำ "ฉบับร่าง" ทุกหน้า"""
    from report_data import build_header, thai_datetime
    from report_template import TEMPLATE

    L = TEMPLATE["labels"]
    hdr = build_header(meeting, participants)
    pdf = _ReportPDF(draft=not approved, watermark=TEMPLATE["draft_watermark"])
    pdf.add_page()

    def sec_header():
        if hdr["org_name"]:
            _line(pdf, hdr["org_name"], 13, bold=True)
        pdf.set_font("Tahoma", "B", 17)
        pdf.multi_cell(0, 10, f"{TEMPLATE['doc_title']} {hdr['title']}", align="C", new_x="LMARGIN", new_y="NEXT")
        if hdr["meeting_no"]:
            _line(pdf, f"{L['meeting_no']} {hdr['meeting_no']}", 13, bold=True)
        when = f"{L['date']} {hdr['date_text']}"
        if hdr["start_time"]:
            when += f"  {L['time']} {hdr['start_time']}"
            if hdr["end_time"]:
                when += f" - {hdr['end_time']}"
            when += f" {L['time_unit']}"
        _line(pdf, when)
        if hdr["venue"]:
            _line(pdf, f"{L['venue']} {hdr['venue']}")
        pdf.ln(2)

    def people_list(people: list[dict], none_text: str):
        if not people:
            _line(pdf, none_text, indent=6)
            return
        for i, p in enumerate(people, start=1):
            suffix = ""
            if p["role"] == "chair":
                suffix = f"  ({L['chair_suffix']})"
            elif p["role"] == "secretary":
                suffix = f"  ({L['secretary_suffix']})"
            _line(pdf, f"{i}. {p['name']}{suffix}", indent=6)

    def sec_attendees():
        _line(pdf, L["attendees"], 12.5, bold=True)
        people_list(hdr["attendees"], L["none"])

    def sec_absent():
        _line(pdf, L["absent"], 12.5, bold=True)
        people_list(hdr["absent"], L["none"])
        pdf.ln(1)

    def sec_opening():
        if hdr["start_time"]:
            _line(pdf, f"{L['opening']} {hdr['start_time']} {L['time_unit']}", bold=True)

    def sec_agenda():
        _line(pdf, L["agenda"], 13, bold=True)
        for i, a in enumerate(content.get("agenda") or [], start=1):
            _line(pdf, f"{L['agenda_item']} {i}  {a.get('title') or ''}", 12, bold=True)
            if a.get("discussion"):
                _line(pdf, a["discussion"], indent=6)
            res = a.get("resolution")
            _line(pdf, f"{L['resolution']}  {res if res else L['no_resolution']}", indent=6, bold=bool(res))
            pdf.ln(1.5)

    def sec_other():
        if content.get("other_matters"):
            _line(pdf, L["other_matters"], 12.5, bold=True)
            _line(pdf, content["other_matters"], indent=6)
            pdf.ln(1)

    def sec_closing():
        if hdr["end_time"]:
            _line(pdf, f"{L['closing']} {hdr['end_time']} {L['time_unit']}", bold=True)
            pdf.ln(1)

    def sec_summary():
        if content.get("summary"):
            _line(pdf, L["summary"], 12.5, bold=True)
            _line(pdf, content["summary"], indent=6)
            pdf.ln(1)

    def sec_actions():
        if pdf.get_y() + 35 > pdf.page_break_trigger:   # กันหัวข้อ+หัวตารางค้างท้ายหน้าโดยไม่มีแถวตามมา
            pdf.add_page()
        _line(pdf, L["action_items"], 12.5, bold=True)
        items = content.get("action_items") or []
        if not items:
            _line(pdf, L["no_action_items"], indent=6)
            return
        col_no, col_task, col_who, col_when = 12, 84, 40, 28
        row_h = 7

        def header_row():
            pdf.set_font("Tahoma", "B", 10)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(col_no, row_h, L["col_no"], border=1, align="C", fill=True)
            pdf.cell(col_task, row_h, L["col_task"], border=1, fill=True)
            pdf.cell(col_who, row_h, L["col_who"], border=1, fill=True)
            pdf.cell(col_when, row_h, L["col_when"], border=1, new_x="LMARGIN", new_y="NEXT", fill=True)

        header_row()
        pdf.set_font("Tahoma", "", 10)
        for i, item in enumerate(items, start=1):
            desc, who, when = item.get("description") or "-", item.get("assignee") or "-", _fmt_due(item)
            lines = max(
                len(pdf.multi_cell(col_task, row_h, desc, dry_run=True, output="LINES")),
                len(pdf.multi_cell(col_who, row_h, who, dry_run=True, output="LINES")),
                len(pdf.multi_cell(col_when, row_h, when, dry_run=True, output="LINES")),
                1,
            )
            h = row_h * lines
            if pdf.get_y() + h > pdf.page_break_trigger:
                pdf.add_page()
                header_row()
                pdf.set_font("Tahoma", "", 10)
            x, y = pdf.get_x(), pdf.get_y()
            pdf.multi_cell(col_no, h, str(i), border=1, align="C")
            pdf.set_xy(x + col_no, y)
            pdf.multi_cell(col_task, row_h, desc, border=1, max_line_height=row_h)
            pdf.set_xy(x + col_no + col_task, y)
            pdf.multi_cell(col_who, row_h, who, border=1, max_line_height=row_h)
            pdf.set_xy(x + col_no + col_task + col_who, y)
            pdf.multi_cell(col_when, row_h, when, border=1, align="C", max_line_height=row_h)
            pdf.set_xy(x, y + h)
        pdf.ln(2)

    def sec_signature():
        sigs = TEMPLATE["signatures"]
        if not sigs:
            return
        pdf.ln(8)
        if pdf.get_y() + 45 > pdf.page_break_trigger:
            pdf.add_page()
        width = (pdf.w - pdf.l_margin - pdf.r_margin) / len(sigs)
        y0 = pdf.get_y()
        for k, sig in enumerate(sigs):
            name = hdr["chair"] if sig["role"] == "chair" else hdr["secretary"] if sig["role"] == "secretary" else None
            x0 = pdf.l_margin + k * width
            pdf.set_font("Tahoma", "", 11)
            pdf.set_xy(x0, y0)
            pdf.cell(width, 7, "ลงชื่อ ........................................", align="C")
            pdf.set_xy(x0, y0 + 8)
            pdf.cell(width, 7, f"({name or '........................................'})", align="C")
            pdf.set_xy(x0, y0 + 16)
            pdf.cell(width, 7, sig["label"], align="C")
        pdf.set_xy(pdf.l_margin, y0 + 26)
        if approved and approved_by:
            pdf.set_font("Tahoma", "", 9)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(
                0, 6,
                TEMPLATE["approval_note"].format(name=approved_by, date=thai_datetime(approved_at)),
                align="C", new_x="LMARGIN", new_y="NEXT",
            )
            pdf.set_text_color(0, 0, 0)

    renderers = {
        "header": sec_header, "attendees": sec_attendees, "absent": sec_absent, "opening": sec_opening,
        "agenda": sec_agenda, "other_matters": sec_other, "closing": sec_closing, "summary": sec_summary,
        "action_items": sec_actions, "signature": sec_signature,
    }
    for name in TEMPLATE["sections"]:
        renderers[name]()
    return bytes(pdf.output())
