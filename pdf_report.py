"""
สร้าง PDF รายงานการประชุมตามแบบฟอร์ม (report_template.py) จากรายงานที่ผ่านการตรวจใน DB

จัดหน้าตามไฟล์ "รูปแบบรายงานการประชุม" (ถอดตำแหน่งจริงจาก PDF ต้นแบบ): กระดาษ A4, ขอบซ้าย/ขวา 72 pt, ฟอนต์ TH Sarabun 16 pt,
ระยะบรรทัด 20.95 pt, หัวรายงานกึ่งกลางตัวหนา + เส้นประ, รายชื่อที่ x=121.7 pt และคอลัมน์ตำแหน่ง/สาเหตุที่ x=330.3 pt,
หัวระเบียบวาระตัวหนาขีดเส้นใต้ชิดขอบซ้าย, ข้อความย่อหน้าบรรทัดแรกเข้า 49.7 pt, เลขหน้ากึ่งกลางด้านบน (หน้า 2 เป็นต้นไป)
หน่วยทั้งหมดในไฟล์นี้เป็น pt (FPDF unit="pt") เพื่อให้เทียบกับต้นแบบได้ตรง ๆ

ฟอนต์: TH Sarabun New (fonts/THSarabunNew*.ttf — ต้นแบบใช้ TH SarabunPSK ซึ่งเมตริกเท่ากัน) เปิด text shaping (ต้องมี uharfbuzz)
เพื่อให้สระ/วรรณยุกต์ซ้อนกันถูกตำแหน่ง
"""

import pathlib

from fpdf import FPDF

HERE = pathlib.Path(__file__).parent
_FONT_REGULAR = HERE / "fonts" / "THSarabunNew.ttf"
_FONT_BOLD = HERE / "fonts" / "THSarabunNew-Bold.ttf"
FONT = "THSarabunNew"
# ฟอนต์ไฟล์นี้ (TH Sarabun New เว็บฟอนต์) กว้างกว่า TH SarabunPSK ที่ต้นแบบใช้ ~1.53 เท่าที่ขนาดเดียวกัน — วัดจากตำแหน่งคำใน PDF ต้นแบบ
# (ความกว้าง "ตามระเบียบวาระ " 76.3 pt เทียบกับ 116.6 pt ที่ 16 pt) จึงคูณขนาดตัวอักษรด้วยสเกลนี้ ให้ขนาด/ตำแหน่งคำตรงกับต้นแบบ
FONT_SCALE = 0.6546

PAGE_W, PAGE_H = 595.28, 841.89      # A4 (pt)
BODY = 16                            # ขนาดตัวอักษรเนื้อหา
PITCH = 20.95                        # ระยะบรรทัด (Word "single" ของ TH SarabunPSK 16 pt)
LEFT = 72.0                          # ขอบซ้ายและตำแหน่งหัวข้อ
RIGHT_X = PAGE_W - 72.0              # ขอบขวา
TOP = 70.7                           # y ของบรรทัดแรก (ปรับให้ baseline ตรง 757.6 pt จากล่างเหมือนต้นแบบ)
INDENT = 121.7                       # รายชื่อ / ข้อย่อย / ย่อหน้าบรรทัดแรก
POS_X = 330.3                        # คอลัมน์ ตำแหน่ง / สาเหตุที่ไม่มา
PARA_GAP = 54.0 - 2 * PITCH          # ช่องว่างเพิ่มหลังบรรทัด "ประธานกล่าวเปิดการประชุม…" ก่อนระเบียบวาระที่ 1
CENTER_L, CENTER_R = 143.0, 397.0    # กึ่งกลางช่องลงชื่อซ้าย/ขวา


class _ReportPDF(FPDF):
    def __init__(self, draft: bool, watermark: str):
        super().__init__(unit="pt", format="A4")
        self.draft = draft
        self.watermark = watermark
        self.add_font(FONT, "", str(_FONT_REGULAR))
        self.add_font(FONT, "B", str(_FONT_BOLD))
        try:
            self.set_text_shaping(True)   # ต้องมี uharfbuzz — ไม่มีก็ยังสร้างได้ แต่วรรณยุกต์ที่ซ้อนสระอาจเพี้ยน
        except Exception:  # noqa: BLE001
            pass
        self.set_margins(LEFT, TOP, PAGE_W - RIGHT_X)
        self.c_margin = 0   # ไม่เว้นขอบในช่อง: ข้อความเริ่มที่ x ตรง ๆ ตามต้นแบบ
        self.set_auto_page_break(auto=True, margin=72)

    def header(self):
        if self.draft:   # ลายน้ำวาดก่อนเนื้อหา เนื้อหาจึงทับอยู่ด้านบน
            with self.rotation(45, x=PAGE_W / 2, y=PAGE_H / 2):
                _font(self, "B", 110)
                self.set_text_color(225, 225, 225)
                self.text(PAGE_W / 2 - 150, PAGE_H / 2 + 35, self.watermark)
            self.set_text_color(0, 0, 0)
        if self.page_no() > 1:   # เลขหน้ากึ่งกลางด้านบน ขนาด 14 (หน้า 1 ไม่แสดง)
            _font(self, "", 14)
            self.set_xy(0, 34.5)
            self.cell(PAGE_W, PITCH, str(self.page_no()), align="C")
        self.set_y(TOP)


def _font(pdf, style: str, size: float):
    """ตั้งฟอนต์ตามขนาดในต้นแบบ (pt ของ TH SarabunPSK) — แปลงด้วย FONT_SCALE ให้ได้ความกว้างตัวอักษรเท่าต้นแบบ"""
    pdf.set_font(FONT, style, size * FONT_SCALE)


def _style(bold: bool, underline: bool) -> str:
    return ("B" if bold else "") + ("U" if underline else "")


def _line(pdf: FPDF, text: str, *, x: float = LEFT, bold: bool = False, underline: bool = False,
          size: float = BODY, align: str = "L", h: float = PITCH):
    """บรรทัด/ย่อหน้าเริ่มที่ x (ข้อความยาวห่อกลับมาที่ x เดิม) — ใช้กับหัวข้อและรายการ"""
    _font(pdf, _style(bold, underline), size)
    if align == "C":
        pdf.set_x(LEFT)
        pdf.multi_cell(RIGHT_X - LEFT, h, text, align="C", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_x(x)
        pdf.multi_cell(RIGHT_X - x, h, text, align="L", new_x="LMARGIN", new_y="NEXT")


def _para(pdf: FPDF, parts, first: float = INDENT):
    """ย่อหน้าตามต้นแบบ: บรรทัดแรกเริ่มที่ first ส่วนบรรทัดถัดไปชิดขอบซ้าย (write() ห่อกลับมาที่ขอบซ้ายเอง)
    parts = ข้อความ หรือลิสต์ของ (ข้อความ, bold, underline) ไว้ผสมสไตล์ในบรรทัดเดียว เช่น "มติที่ประชุม:" ตัวหนาขีดเส้นใต้"""
    if isinstance(parts, str):
        parts = [(parts, False, False)]
    pdf.set_x(first)
    for text, bold, underline in parts:
        _font(pdf, _style(bold, underline), BODY)
        pdf.write(PITCH, text)
    pdf.ln(PITCH)


def _blank(pdf: FPDF, lines: float = 1):
    pdf.ln(PITCH * lines)


def _dots(pdf: FPDF, x: float, reserve: float = 0) -> str:
    """เส้นประยาวถึงขอบขวา (เริ่มที่ x)"""
    _font(pdf, "", BODY)
    n = int((RIGHT_X - x - reserve) / pdf.get_string_width("."))
    return "." * max(n, 3)


def _fmt_due(item: dict) -> str:
    when = str(item["due_date"]) if item.get("due_date") else "-"
    if item.get("due_time"):
        when += f" {item['due_time']}"
        if item.get("due_time_end"):
            when += f"-{item['due_time_end']}"
    return when


def _subject(title: str | None) -> str:
    """"รายงานการประชุม" + ชื่อเรื่อง — ตัดคำว่า "การประชุม/ประชุม" ที่ขึ้นต้นชื่อเรื่องออก ไม่ให้ซ้ำเป็น "รายงานการประชุมประชุม…" """
    t = (title or "").strip()
    for prefix in ("การประชุม", "ประชุม"):
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
            break
    return t


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
    from report_data import build_header, thai_datetime, thai_time
    from report_template import TEMPLATE

    L = TEMPLATE["labels"]
    hdr = build_header(meeting, participants)
    pdf = _ReportPDF(draft=not approved, watermark=TEMPLATE["draft_watermark"])
    pdf.add_page()

    def sec_header():
        # รายงานการประชุม… / ครั้งที่… / เมื่อวันที่… / ณ… จัดกึ่งกลางตัวหนา แล้วเส้นประ (ขนาด 18) ตามต้นแบบ
        subject = _subject(hdr["title"])
        _line(pdf, f"{TEMPLATE['doc_title']}{(' ' + subject) if subject else ''}", bold=True, align="C")
        _line(pdf, f"{L['meeting_no']} {hdr['meeting_no'] or '......./................'}", bold=True, align="C")
        _line(pdf, f"{L['date']} {hdr['date_text']}", bold=True, align="C")
        place = " ".join(x for x in (hdr["venue"], hdr["org_name"]) if x)
        _line(pdf, f"{L['venue']} {place}" if place else f"{L['venue']} ห้องประชุม ........................................", bold=True, align="C")
        _line(pdf, L["rule"], bold=True, size=18, align="C", h=PITCH + 2.7)

    def people_list(people: list[dict], none_text: str, *, third: str | None):
        """third: "position" = คอลัมน์ ตำแหน่ง (ผู้มาประชุม) / "reason" = คอลัมน์สาเหตุ (ผู้ไม่มา) / None = มีแต่ชื่อ"""
        if not people:
            _line(pdf, none_text, x=INDENT)
            return
        for i, p in enumerate(people, start=1):
            y = pdf.get_y()
            _font(pdf, "", BODY)
            pdf.set_xy(INDENT, y)
            pdf.cell(POS_X - INDENT - 6, PITCH, f"{i}. {p['name']}")
            if third == "position":
                role = {"chair": L["chair_suffix"], "secretary": L["secretary_suffix"],
                        "attendee": L["member_suffix"]}.get(p["role"], "")
                text = f"{L['position']} {role}".strip() if role else ""
            elif third == "reason":
                text = p.get("reason") or ""
            else:
                text = ""
            if text:
                pdf.set_xy(POS_X, y)
                pdf.multi_cell(RIGHT_X - POS_X, PITCH, text, align="L", new_x="LMARGIN", new_y="NEXT")
            else:
                pdf.set_xy(LEFT, y + PITCH)

    def sec_attendees():
        _line(pdf, L["attendees"], bold=True)
        people_list(hdr["attendees"], L["none"], third="position")
        _blank(pdf)

    def sec_guests():
        if hdr["guests"]:   # ผู้เข้าร่วมประชุม (ถ้ามี) — ไม่มีก็ไม่ต้องแสดงหัวข้อ
            _line(pdf, L["guests"], bold=True)
            people_list(hdr["guests"], L["none"], third=None)
            _blank(pdf)

    def sec_absent():
        _line(pdf, L["absent"], bold=True)
        people_list(hdr["absent"], L["none"], third="reason")
        _blank(pdf)

    def _time_line(label: str, hhmm: str | None):
        t = f"{thai_time(hhmm)} {L['time_unit']}" if hhmm else f"................ {L['time_unit']}"
        _line(pdf, f"{label} {t}", bold=True)

    def sec_opening():
        _time_line(L["opening"], hdr["start_time"])
        _line(pdf, L["opening_line"], x=INDENT)
        pdf.ln(PITCH + PARA_GAP)

    def _agenda_heading(n: int, title: str):
        _line(pdf, f"{L['agenda_item']} {n} {title}", bold=True, underline=True)

    def _resolution(text: str | None, *, blank: bool = False):
        parts = [(L["resolution"], True, True)]
        if blank:
            parts.append((" " + _dots(pdf, INDENT + 90), False, False))
        else:
            parts.append((" " + (text if text else L["no_resolution"]), bool(text), False))
        _para(pdf, parts)

    def sec_agenda():
        for n, entry in enumerate(TEMPLATE["agenda"], start=1):
            _agenda_heading(n, entry["title"])
            kind = entry["kind"]
            if kind == "blank":
                _line(pdf, _dots(pdf, INDENT), x=INDENT)
            elif kind == "approve":
                _resolution(None, blank=True)
            elif kind == "consider":
                _line(pdf, f"{n}.1 {entry['old']}", x=INDENT, bold=True)
                _line(pdf, _dots(pdf, INDENT), x=INDENT)
                _blank(pdf)
                _line(pdf, f"{n}.2 {entry['new']}", x=INDENT, bold=True)
                items = content.get("agenda") or []
                if not items:
                    _line(pdf, _dots(pdf, INDENT), x=INDENT)
                for k, a in enumerate(items, start=1):
                    _line(pdf, f"{n}.2.{k} {a.get('title') or ''}", x=INDENT)
                    if a.get("discussion"):
                        _para(pdf, a["discussion"])
                    _resolution(a.get("resolution"))
                    if k < len(items):
                        _blank(pdf)
            elif kind == "other":
                if content.get("other_matters"):
                    _para(pdf, content["other_matters"])
                else:
                    _line(pdf, L["other_none"], x=INDENT)
            _blank(pdf)

    def sec_closing():
        _time_line(L["closing"], hdr["end_time"])
        _blank(pdf, 1.3)

    def sec_summary():
        if content.get("summary"):
            _line(pdf, L["summary"], bold=True)
            _para(pdf, content["summary"])
            _blank(pdf)

    def sec_actions():
        if pdf.get_y() + 90 > pdf.page_break_trigger:   # กันหัวข้อ+หัวตารางค้างท้ายหน้าโดยไม่มีแถวตามมา
            pdf.add_page()
        _line(pdf, L["action_items"], bold=True)
        items = content.get("action_items") or []
        if not items:
            _line(pdf, L["no_action_items"], x=INDENT)
            _blank(pdf)
            return
        col_no, col_who, col_when = 40, 110, 73
        col_task = RIGHT_X - LEFT - col_no - col_who - col_when
        row_h = 20.0
        size = 14

        def header_row():
            _font(pdf, "B", size)
            pdf.set_fill_color(230, 230, 230)
            pdf.set_x(LEFT)
            pdf.c_margin = 4
            pdf.cell(col_no, row_h, L["col_no"], border=1, align="C", fill=True)
            pdf.cell(col_task, row_h, L["col_task"], border=1, fill=True)
            pdf.cell(col_who, row_h, L["col_who"], border=1, fill=True)
            pdf.cell(col_when, row_h, L["col_when"], border=1, new_x="LMARGIN", new_y="NEXT", fill=True)
            pdf.c_margin = 0

        header_row()
        _font(pdf, "", size)
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
                _font(pdf, "", size)
            y = pdf.get_y()
            # เส้นขอบวาดเป็นสี่เหลี่ยมความสูงเท่ากันทั้งแถว (multi_cell วาดขอบตามจำนวนบรรทัดของแต่ละช่อง ทำให้ช่องสั้นขอบไม่เท่ากัน)
            cx = LEFT
            for width, txt, align in ((col_no, str(i), "C"), (col_task, desc, "L"), (col_who, who, "L"), (col_when, when, "C")):
                pdf.rect(cx, y, width, h)
                pdf.set_xy(cx + 4, y)          # เว้นขอบในช่อง 4 pt (c_margin ตั้งเป็น 0 เพื่อให้ข้อความนอกตารางเริ่มที่ x ตรง ๆ)
                pdf.multi_cell(width - 8, row_h, txt, align=align, max_line_height=row_h, new_x="LEFT", new_y="TOP")
                cx += width
            pdf.set_xy(LEFT, y + h)
        _blank(pdf)

    def sec_signature():
        sigs = TEMPLATE["signatures"]
        if not sigs:
            return
        if pdf.get_y() + 3 * PITCH > pdf.page_break_trigger:
            pdf.add_page()
        y0 = pdf.get_y()
        w = 150.0
        for sig, cx in zip(sigs, (CENTER_L, CENTER_R)):
            name = hdr["chair"] if sig["role"] == "chair" else hdr["secretary"] if sig["role"] == "secretary" else None
            _font(pdf, "", BODY)
            pdf.set_xy(cx - w / 2, y0)
            pdf.cell(w, PITCH, f"({name or '.' * 28})", align="C")
            pdf.set_xy(cx - w / 2, y0 + PITCH)
            pdf.cell(w, PITCH, sig["label"], align="C")
        pdf.set_xy(LEFT, y0 + 2.4 * PITCH)
        if approved and approved_by:
            _font(pdf, "", 12)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(
                0, 16,
                TEMPLATE["approval_note"].format(name=approved_by, date=thai_datetime(approved_at)),
                align="C", new_x="LMARGIN", new_y="NEXT",
            )
            pdf.set_text_color(0, 0, 0)

    renderers = {
        "header": sec_header, "attendees": sec_attendees, "guests": sec_guests, "absent": sec_absent,
        "opening": sec_opening, "agenda": sec_agenda, "action_items": sec_actions, "closing": sec_closing,
        "summary": sec_summary, "signature": sec_signature,
    }
    for name in TEMPLATE["sections"]:
        renderers[name]()
    return bytes(pdf.output())
