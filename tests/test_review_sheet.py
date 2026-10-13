"""ทดสอบใบตรวจคุณภาพรายงาน AI (ฟังก์ชันล้วน ไม่ต่อ DB)"""

import datetime
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import review_sheet as rs  # noqa: E402

AI = {
    "summary": "สรุปเดิม",
    "agenda": [
        {"section": "inform", "title": "เรื่อง A", "discussion": "คุยเรื่อง A", "resolution": None, "evidence": ["ก"]},
        {"section": "consider_new", "title": "เรื่อง B", "discussion": "คุยเรื่อง B", "resolution": "อนุมัติ", "evidence": ["ข"]},
        {"section": "consider_new", "title": "เรื่อง C", "discussion": "คุยเรื่อง C", "resolution": None, "evidence": []},
    ],
    "action_items": [
        {"description": "ส่งเอกสาร", "assignee": "สมชาย", "due_date": "2026-10-20", "due_time": None, "due_time_end": None},
        {"description": "จองห้อง", "assignee": "สมหญิง", "due_date": None, "due_time": None, "due_time_end": None},
    ],
}


def edited():
    c = {k: (list(v) if isinstance(v, list) else v) for k, v in AI.items()}
    c["agenda"] = [dict(a) for a in AI["agenda"]]
    c["action_items"] = [dict(a) for a in AI["action_items"]]
    return c


class ReviewSheetTests(unittest.TestCase):
    def test_no_snapshot_means_nothing_to_compare(self):
        self.assertEqual(rs.compare(None, edited()), {"available": False})

    def test_untouched_report_has_no_human_edits(self):
        d = rs.compare(AI, edited())
        self.assertFalse(d["summary_changed"])
        self.assertEqual((d["agenda"]["unchanged"], d["agenda"]["changed"], d["agenda"]["added_by_human"], d["agenda"]["removed_by_human"]),
                         (3, [], [], []))
        self.assertEqual(d["actions"]["unchanged"], 2)

    def test_human_changes_are_counted_by_kind(self):
        c = edited()
        c["summary"] = "สรุปที่คนแก้"
        c["agenda"][1]["resolution"] = None                       # คนลบมติที่ AI แต่งขึ้น
        c["agenda"].pop(2)                                        # คนลบวาระ C
        c["agenda"].append({"section": "inform", "title": "เรื่อง D", "discussion": "ใหม่", "resolution": None})   # คนเพิ่มวาระ D
        c["action_items"][0]["assignee"] = "สมศรี"                 # คนแก้ผู้รับผิดชอบ
        c["action_items"][1]["description"] = "จองห้องประชุม"       # เปลี่ยนชื่อ = ลบ+เพิ่ม
        d = rs.compare(AI, c)
        self.assertTrue(d["summary_changed"])
        self.assertEqual(d["agenda"]["changed"], ["เรื่อง B"])
        self.assertEqual((d["agenda"]["added_by_human"], d["agenda"]["removed_by_human"]), (["เรื่อง D"], ["เรื่อง C"]))
        self.assertEqual(d["actions"]["changed"], ["ส่งเอกสาร"])
        self.assertEqual((d["actions"]["added_by_human"], d["actions"]["removed_by_human"]), (["จองห้องประชุม"], ["จองห้อง"]))

    def test_whitespace_only_differences_are_ignored(self):
        c = edited()
        c["agenda"][0]["discussion"] = "คุยเรื่อง   A "
        self.assertEqual(rs.compare(AI, c)["agenda"]["changed"], [])

    def test_sheet_lists_every_item_with_evidence_and_blank_checks(self):
        meeting = {"meeting_id": 7, "title": "ประชุมทดสอบ"}
        report = {"content": edited(), "ai_snapshot": AI, "model_used": "m", "prompt_version": "minutes_v4", "generated_at": "t"}
        rows = [{"display_name": "สมชาย", "text": "สวัสดีครับ", "spoken_at": datetime.datetime(2026, 10, 13, 9, 0, 5)}]
        text = rs.build_sheet(meeting, report, rows)
        for needle in ("ประชุมทดสอบ (id 7)", "### วาระที่ 3: เรื่อง C", "### งานที่ 2: จองห้อง", "> อ้างอิง: ข", "**แต่งมติ**",
                       "**เดาวัน**", "[09:00:05] สมชาย: สวัสดีครับ", "ไม่ถูกแก้ 3"):
            self.assertIn(needle, text)
        self.assertEqual(text.count("### วาระที่"), 3)
        self.assertEqual(text.count("### งานที่"), 2)


if __name__ == "__main__":
    unittest.main()
