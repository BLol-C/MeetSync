"""ทดสอบเครื่องมือเทียบบทพูดกับ transcript ของการทดสอบเสถียรภาพ (ฟังก์ชันล้วน ไม่ต่อ DB/Meet)"""

import datetime
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import stability_check as sc  # noqa: E402


def rows_for(texts, start=datetime.datetime(2026, 10, 12, 18, 0, 0)):
    return [{"text": t, "spoken_at": start + datetime.timedelta(seconds=i * 40)} for i, t in enumerate(texts)]


class StabilityCheckTests(unittest.TestCase):
    def test_script_has_90_unique_lines_and_round_trips_through_the_file(self):
        lines = sc.make_lines()
        self.assertEqual(len(lines), 90)
        self.assertEqual(len(set(lines)), 90)                                        # ไม่มีประโยคซ้ำ จึงจับคู่ได้ทีละประโยค
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / "script.txt"
            path.write_text(sc.script_text(lines), encoding="utf-8")
            self.assertEqual(sc.read_script(path), lines)                            # หัวเอกสารไม่ถูกนับเป็นประโยค

    def test_perfect_noisy_and_missing_transcripts(self):
        lines = sc.make_lines()
        text, passed = sc.report(lines, rows_for(lines))
        self.assertTrue(passed)
        self.assertIn("จับได้ 90/90 = 100.0%", text)
        noisy = [l.replace("ครับ", "คับ").replace(" ", "") for l in lines]          # CC ถอดคำผิดเล็กน้อย/เว้นวรรคต่างจากบท
        self.assertTrue(sc.report(lines, rows_for(noisy))[1])
        gap = lines[:30] + lines[50:]                                                # บอทหลุดกลางประชุม 20 ประโยค (~13 นาที)
        text, passed = sc.report(lines, rows_for(gap))
        self.assertFalse(passed)
        self.assertIn("ช่วงที่หายติดต่อกันยาวสุด: 20 ประโยค", text)
        self.assertIn("31 (20:00)", text)                                            # บอกว่าหายตั้งแต่นาทีที่เท่าไร

    def test_a_single_missing_line_is_reported_by_number(self):
        lines = sc.make_lines()
        text, passed = sc.report(lines, rows_for(lines[:40] + lines[41:]))           # หายแค่ประโยคที่ 41
        self.assertIn("จับได้ 89/90", text)
        self.assertIn("41 (26:40)", text)
        self.assertTrue(passed)                                                       # ยังผ่านเกณฑ์ 95% (หายประโยคเดียว)

    def test_duplicate_segments_are_counted_ignoring_spaces(self):
        self.assertEqual(sc.duplicate_segments(["สวัสดี ครับ", "สวัสดีครับ", "ขอบคุณครับ"]), 1)


if __name__ == "__main__":
    unittest.main()
