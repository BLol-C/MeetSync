"""ทดสอบตัวชี้วัดประเมินคุณภาพ AI: ต้องให้ 100% กับคำตอบสมบูรณ์ และต้องจับความผิดแต่ละประเภทได้จริง"""

import copy
import json
import pathlib
import unittest

from evaluation import metrics, run_eval

CASE_DIR = pathlib.Path(run_eval.CASES_DIR) / "example_budget_meeting"
CASE = run_eval.load_case(CASE_DIR)
NAMES = [p["display_name"] for p in CASE["participants"]]

PERFECT = {
    "agenda": [
        {"title": "งบประมาณ", "discussion": "ที่ประชุมพิจารณางบสำหรับซื้อเซิร์ฟเวอร์ทดสอบ",
         "resolution": "ที่ประชุมอนุมัติงบสองหมื่นบาท", "grounded": True},
        {"title": "กำหนดส่งงาน", "discussion": "รายงานความคืบหน้าส่วนหน้าเว็บ", "resolution": None, "grounded": None},
        {"title": "ห้องประชุมสำหรับนำเสนอ", "discussion": "ยังไม่ได้ข้อสรุปเรื่องห้องประชุมนำเสนอ", "resolution": None, "grounded": None},
    ],
    "action_items": [
        {"description": "เปรียบเทียบราคาเซิร์ฟเวอร์สามเจ้า", "assignee": "Bob", "due_date": "2026-10-05", "grounded": True},
        {"description": "ส่งรายงานความก้าวหน้าฉบับสมบูรณ์", "assignee": "Alice", "due_date": "2026-10-09", "grounded": True},
    ],
}


def score(content):
    return metrics.evaluate(content, CASE["expected"], NAMES)


class MetricTests(unittest.TestCase):
    def test_perfect_output_scores_full_marks(self):
        m = score(PERFECT)
        for key in ("agenda_recall", "resolution_accuracy", "action_recall", "action_precision",
                    "assignee_accuracy", "date_accuracy"):
            self.assertEqual(m[key], 1.0, key)
        self.assertEqual(m["false_resolution_rate"], 0.0)
        self.assertEqual(m["ungrounded_rate"], 0.0)
        self.assertEqual(m["unflagged_wrong"], 0)
        self.assertTrue(all(v == "ผ่าน" for v in metrics.verdict(m).values()))

    def test_missing_agenda_and_actions_lower_recall(self):
        c = copy.deepcopy(PERFECT)
        c["agenda"] = c["agenda"][:1]
        c["action_items"] = c["action_items"][:1]
        m = score(c)
        self.assertAlmostEqual(m["agenda_recall"], 1 / 3)
        self.assertAlmostEqual(m["action_recall"], 0.5)
        self.assertEqual(m["action_precision"], 1.0)

    def test_invented_resolution_is_caught(self):
        c = copy.deepcopy(PERFECT)
        c["agenda"][2]["resolution"] = "ที่ประชุมมีมติใช้ห้องประชุมใหญ่"      # ความจริง: ยังไม่มีข้อสรุป
        m = score(c)
        self.assertEqual(m["false_resolution_rate"], 0.5)                     # 1 ใน 2 วาระที่ไม่มีมติจริง
        self.assertLess(m["resolution_accuracy"], 1.0)
        self.assertEqual(metrics.verdict(m)["false_resolution_rate"], "ไม่ผ่าน")

    def test_wrong_assignee_and_date(self):
        c = copy.deepcopy(PERFECT)
        c["action_items"][0]["assignee"] = "Alice"
        c["action_items"][1]["due_date"] = "2026-10-16"
        m = score(c)
        self.assertEqual(m["assignee_accuracy"], 0.5)
        self.assertEqual(m["date_accuracy"], 0.5)

    def test_invented_task_lowers_precision_and_counts_as_unflagged_when_validation_missed_it(self):
        c = copy.deepcopy(PERFECT)
        c["action_items"].append({"description": "จัดซื้อโน้ตบุ๊กสิบเครื่อง", "assignee": "Bob",
                                  "due_date": None, "grounded": True})       # ผิดแต่ระบบตรวจไม่เจอ
        m = score(c)
        self.assertAlmostEqual(m["action_precision"], 2 / 3)
        self.assertEqual(m["unflagged_wrong"], 1)
        c["action_items"][-1]["grounded"] = False                            # ถ้าระบบตรวจเจอ -> ไม่นับเป็นช่องโหว่
        m = score(c)
        self.assertEqual(m["unflagged_wrong"], 0)
        self.assertGreater(m["ungrounded_rate"], 0)

    def test_keyword_matching_ignores_spacing_and_case(self):
        self.assertEqual(metrics.kw_score("ซื้อ Server ทดสอบ", ["server", "ซื้อ"]), 1.0)
        self.assertEqual(metrics.kw_score("", ["x"]), 0.0)
        self.assertEqual(metrics.kw_score("abc", []), 0.0)

    def test_synonym_lists_accept_any_spelling_of_a_number(self):
        kws = ["อนุมัติ", ["สองหมื่น", "20,000", "20000"]]
        self.assertEqual(metrics.kw_score("ที่ประชุมอนุมัติงบ 20,000 บาท", kws), 1.0)
        self.assertEqual(metrics.kw_score("อนุมัติงบสองหมื่นบาท", kws), 1.0)
        self.assertEqual(metrics.kw_score("อนุมัติงบห้าพันบาท", kws), 0.5)

    def test_average_skips_missing_values(self):
        avg = metrics.average([{"agenda_recall": 1.0}, {"agenda_recall": 0.5}, {"agenda_recall": None}])
        self.assertEqual(avg["agenda_recall"], 0.75)
        self.assertIsNone(avg["date_accuracy"])


class CaseFileTests(unittest.TestCase):
    def test_example_case_loads(self):
        self.assertTrue(CASE["synthetic"])
        self.assertGreater(len(CASE["rows"]), 8)
        self.assertEqual(CASE["participants"][0]["role"], "chair")
        self.assertTrue(json.loads((CASE_DIR / "case.json").read_text(encoding="utf-8"))["expected"]["agenda"])

    def test_every_expected_keyword_actually_appears_in_the_transcript(self):
        """กันเฉลยพิมพ์ผิด: คำสำคัญของเฉลยต้องมีในเนื้อหาประชุมจริง ไม่งั้นต่อให้ AI เก่งแค่ไหนก็ได้คะแนนไม่เต็ม"""
        text = "".join(r["text"] for r in CASE["rows"])
        for a in CASE["expected"]["agenda"]:
            self.assertGreaterEqual(metrics.kw_score(text, a["topic"]), metrics.MATCH_THRESHOLD, a)
        for t in CASE["expected"]["action_items"]:
            self.assertGreaterEqual(metrics.kw_score(text, t["keywords"]), metrics.MATCH_THRESHOLD, t)


if __name__ == "__main__":
    unittest.main()
