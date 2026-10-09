"""ตัดคำไทยใน PDF: ห่อบรรทัดต้องตัดที่รอยต่อคำ ไม่ตัดกลางคำ และไม่มีอักขระซ่อนหลุดเข้าไปในข้อความของ PDF
ไม่ใช้ DB / ไม่เรียก AI
"""

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fpdf import FPDF  # noqa: E402

from reports import pdf_report  # noqa: E402
from reports.thai_text import ZWSP, break_thai, strip_breaks, word_tokenize  # noqa: E402

NL = chr(10)
TEXT = "มหาวิทยาลัยเกษตรศาสตร์วิทยาเขตกำแพงแสนคณะศิลปศาสตร์และวิทยาศาสตร์พิจารณางบประมาณประจำปีการศึกษาสองพันห้าร้อยหกสิบเก้า"
WIDTH = 125.0       # แคบพอให้ห่อหลายบรรทัด แต่กว้างกว่าคำที่ยาวที่สุด ("มหาวิทยาลัยเกษตรศาสตร์" ≈ 110 pt)


def word_ends(text: str) -> set[int]:
    ends, pos = set(), 0
    for tok in word_tokenize(text, engine="newmm"):
        pos += len(tok)
        ends.add(pos)
    return ends


def line_ends(lines: list[str]) -> set[int]:
    total, ends = 0, set()
    for line in lines[:-1]:
        total += len(line.replace(" ", ""))
        ends.add(total)
    return ends


def new_report_pdf():
    pdf = pdf_report._ReportPDF(draft=False, watermark="x")
    pdf.add_page()
    pdf_report._font(pdf, "", 16)
    return pdf


def bare_fpdf():
    """FPDF ตัวเปล่า (ไม่ผ่านโค้ดของเรา) ตั้งค่าเหมือน _ReportPDF — ใช้เทียบว่าเดิมตัดกลางคำจริง
    (ห้ามเรียก FPDF.multi_cell(pdf_ของเรา, ...) เพราะ fpdf2 เรียก self.multi_cell ซ้ำภายใน จะวิ่งกลับมาผ่านโค้ดของเรา)"""
    pdf = FPDF(unit="pt", format="A4")
    pdf.add_font(pdf_report.FONT, "", str(pdf_report._FONT_REGULAR))
    pdf.set_text_shaping(True)
    pdf.c_margin = 0
    pdf.add_page()
    pdf.set_font(pdf_report.FONT, "", 16 * pdf_report.FONT_SCALE)
    return pdf


@unittest.skipIf(word_tokenize is None, "ไม่ได้ติดตั้ง pythainlp")
class ThaiBreakTests(unittest.TestCase):
    def test_zwsp_goes_between_words_and_removing_it_restores_the_text(self):
        out = break_thai(TEXT)
        self.assertIn(ZWSP, out)
        self.assertEqual(strip_breaks(out), TEXT)
        self.assertTrue(out.startswith("มหาวิทยาลัยเกษตรศาสตร์" + ZWSP))

    def test_non_thai_whitespace_and_non_strings_are_left_alone(self):
        self.assertEqual(break_thai("hello world 2569"), "hello world 2569")
        self.assertEqual(break_thai(""), "")
        self.assertIsNone(break_thai(None))
        self.assertIn(NL, break_thai("ส่งงาน" + NL + "วันศุกร์"))

    def test_repetition_mark_stays_with_its_word(self):
        self.assertNotIn(ZWSP + "ๆ", break_thai("เห็นชอบเห็นชอบๆ"))

    def test_without_the_fix_the_text_is_cut_mid_word(self):
        lines = bare_fpdf().multi_cell(WIDTH, 20, TEXT, dry_run=True, output="LINES")
        self.assertFalse(line_ends(lines) <= word_ends(TEXT), "เทสต์นี้ต้องจับความต่างได้: ของเดิมตัดกลางคำ")

    def test_multi_cell_breaks_only_between_words_and_draws_no_hidden_characters(self):
        drawn = []
        real = FPDF.multi_cell

        def spy(self, w, h=None, text="", *a, **k):
            if not k.get("dry_run") and k.get("output") is None:    # เฉพาะการวาดจริง (ไม่ใช่การคำนวณบรรทัดภายใน)
                drawn.append(text)
            return real(self, w, h, text, *a, **k)

        pdf = new_report_pdf()
        with mock.patch.object(FPDF, "multi_cell", spy):
            pdf.multi_cell(WIDTH, 20, TEXT)
        self.assertEqual(len(drawn), 1)
        self.assertNotIn(ZWSP, drawn[0])                         # ข้อความที่วาดจริงไม่มี ZWSP (ค้นหา/คัดลอกจาก PDF ได้ตรง)
        lines = drawn[0].split(NL)
        self.assertGreater(len(lines), 2)
        self.assertEqual("".join(lines), TEXT)
        self.assertTrue(line_ends(lines) <= word_ends(TEXT), "ตัดกลางคำ")

    def test_write_paragraphs_break_only_between_words(self):
        drawn = []
        real = FPDF.write

        def spy(self, h=None, text="", *a, **k):
            drawn.append((text, self.y))
            return real(self, h, text, *a, **k)

        pdf = new_report_pdf()
        long_text = TEXT * 2
        pdf.set_x(pdf_report.INDENT)
        with mock.patch.object(FPDF, "write", spy):
            pdf.write(pdf_report.PITCH, long_text)
        texts = [t for t, _ in drawn]
        self.assertNotIn(ZWSP, "".join(texts))
        self.assertEqual("".join(texts), long_text)
        rows = []                                                # รวมชิ้นที่อยู่แถว (y) เดียวกันเป็นบรรทัด
        for text, y in drawn:
            if rows and abs(rows[-1][0] - y) < 0.01:
                rows[-1][1] += text
            else:
                rows.append([y, text])
        lines = [r[1] for r in rows]
        self.assertGreater(len(lines), 2)
        self.assertTrue(line_ends(lines) <= word_ends(long_text), "ตัดกลางคำ")

    def test_text_in_the_pdf_has_no_hidden_characters(self):
        import io

        from pypdf import PdfReader
        pdf = new_report_pdf()
        pdf.multi_cell(WIDTH, 20, TEXT)
        extracted = PdfReader(io.BytesIO(bytes(pdf.output()))).pages[0].extract_text()
        self.assertNotIn(ZWSP, extracted)


if __name__ == "__main__":
    unittest.main()
