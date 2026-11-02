"""เทียบสองรุ่นของรายงาน (reports/history.py) — ฟังก์ชันล้วน ไม่ใช้ฐานข้อมูล"""

import unittest

from reports import history


def report(**over):
    base = {
        "summary": "ที่ประชุมอนุมัติงบสองหมื่นบาท",
        "other_matters": None,
        "agenda": [{"section": "consider_new", "title": "งบประมาณ", "discussion": "เสนอให้อนุมัติงบ",
                    "resolution": "อนุมัติงบสองหมื่นบาท", "evidence": ["x"]}],
        "action_items": [{"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-09",
                          "due_time": "13:00", "due_time_end": None, "evidence": []}],
    }
    base.update(over)
    return base


def text_of(ops, *kinds):
    return "".join(t for op, t in ops if op in kinds)


class KindLabelTests(unittest.TestCase):
    def test_every_kind_the_database_can_hold_has_a_thai_label(self):
        import db
        for kind in db.VERSION_KINDS:
            self.assertRegex(history.KIND_LABEL[kind], r"[฀-๿]")

    def test_unknown_kind_never_shows_raw_english(self):
        self.assertNotRegex(history.OTHER_KIND, r"[a-zA-Z]")


class TextDiffTests(unittest.TestCase):
    def test_same_text_is_one_equal_piece(self):
        self.assertEqual(history.text_diff("สวัสดี", "สวัสดี"), [("equal", "สวัสดี")])

    def test_old_and_new_text_are_recoverable_from_the_pieces(self):
        old, new = "อนุมัติงบสองหมื่นบาท ภายในวันที่ 12", "อนุมัติงบสามหมื่นบาท ภายในวันที่ 15"
        ops = history.text_diff(old, new)
        self.assertEqual(text_of(ops, "equal", "del"), old)
        self.assertEqual(text_of(ops, "equal", "ins"), new)

    def test_tiny_coincidental_matches_do_not_chop_the_change_into_confetti(self):
        ops = history.text_diff("กขคงจฉช", "ซฌญฎกฏฐ")        # แทบไม่มีส่วนที่ตรงกัน
        self.assertLessEqual(len(ops), 3)

    def test_none_is_treated_as_empty(self):
        self.assertEqual(history.text_diff(None, "ใหม่"), [("ins", "ใหม่")])
        self.assertEqual(history.text_diff("เก่า", None), [("del", "เก่า")])
        self.assertEqual(history.text_diff(None, None), [])


class DiffReportsTests(unittest.TestCase):
    def test_identical_reports_have_no_change(self):
        d = history.diff_reports(report(), report())
        self.assertFalse(d["changed"])
        self.assertEqual({a["status"] for a in d["agenda"]}, {"same"})

    def test_edited_resolution_is_a_change_of_the_same_agenda_item(self):
        new = report(agenda=[{**report()["agenda"][0], "resolution": "อนุมัติงบสามหมื่นบาท"}])
        d = history.diff_reports(report(), new)
        self.assertTrue(d["changed"])
        self.assertEqual([a["status"] for a in d["agenda"]], ["changed"])
        self.assertEqual(text_of(d["agenda"][0]["resolution"], "del"), "อง")
        self.assertEqual(text_of(d["agenda"][0]["resolution"], "ins"), "าม")

    def test_renamed_item_is_paired_not_reported_as_remove_plus_add(self):
        new = report(agenda=[{**report()["agenda"][0], "title": "งบประมาณปี 2570"}])
        self.assertEqual([a["status"] for a in history.diff_reports(report(), new)["agenda"]], ["changed"])

    def test_title_rewritten_beyond_recognition_is_still_the_same_item_when_alone_in_its_section(self):
        old = report(agenda=[{**report()["agenda"][0], "title": "งบประมาณ", "discussion": ""}])
        new = report(agenda=[{**report()["agenda"][0], "title": "งบประมาณโครงการ (แก้โดยคน)", "discussion": ""}])
        self.assertEqual([a["status"] for a in history.diff_reports(old, new)["agenda"]], ["changed"])
        elsewhere = report(agenda=[{**new["agenda"][0], "section": "inform"}])      # คนละหมวดและชื่อไม่เหลือส่วนที่เหมือน = คนละเรื่อง
        self.assertEqual(sorted(a["status"] for a in history.diff_reports(old, elsewhere)["agenda"]), ["added", "removed"])

    def test_empty_discussions_do_not_make_unrelated_items_look_alike(self):
        base = {"section": "consider_new", "discussion": "", "resolution": None, "evidence": []}
        old = report(agenda=[{**base, "title": "เรื่องแรก"}, {**base, "title": "เรื่องที่สอง"}])
        new = report(agenda=[{**base, "title": "เรื่องที่สอง"}, {**base, "title": "เรื่องสุดท้ายที่ใหม่"}])
        # "เรื่องที่สอง" ตรงกันเป๊ะ; ที่เหลือฝั่งละหนึ่งในหมวดเดียวกันถูกจับคู่เป็นเรื่องที่ถูกแก้ ไม่ใช่เพราะส่วนที่ว่างเปล่าเหมือนกัน
        self.assertEqual([a["status"] for a in history.diff_reports(old, new)["agenda"]], ["same", "changed"])

    def test_added_and_removed_items(self):
        extra = {"section": "inform", "title": "แจ้งเรื่องอื่นที่ไม่เกี่ยวกันเลย", "discussion": "", "resolution": None, "evidence": []}
        added = history.diff_reports(report(), report(agenda=report()["agenda"] + [extra]))
        self.assertEqual([a["status"] for a in added["agenda"]], ["same", "added"])
        removed = history.diff_reports(report(agenda=report()["agenda"] + [extra]), report())
        self.assertEqual(sorted(a["status"] for a in removed["agenda"]), ["removed", "same"])

    def test_moving_an_item_between_sections_counts_as_a_change(self):
        new = report(agenda=[{**report()["agenda"][0], "section": "inform"}])
        a = history.diff_reports(report(), new)["agenda"][0]
        self.assertEqual((a["status"], a["section_changed"], a["old_section"], a["section"]),
                         ("changed", True, "consider_new", "inform"))

    def test_action_item_date_and_time_changes_show_in_when(self):
        new = report(action_items=[{**report()["action_items"][0], "due_date": "2026-10-12", "due_time_end": "15:00"}])
        d = history.diff_reports(report(), new)
        self.assertEqual(d["action_items"][0]["status"], "changed")
        self.assertEqual(text_of(d["action_items"][0]["when"], "equal", "del"), "2026-10-09 13:00")
        self.assertEqual(text_of(d["action_items"][0]["when"], "equal", "ins"), "2026-10-12 13:00–15:00")

    def test_end_date_change_shows_in_when(self):
        new = report(action_items=[{**report()["action_items"][0], "due_date_end": "2026-10-11"}])
        d = history.diff_reports(report(), new)
        self.assertEqual(d["action_items"][0]["status"], "changed")
        self.assertIn("ถึง 2026-10-11", text_of(d["action_items"][0]["when"], "equal", "ins"))
        old_style = {k: v for k, v in report()["action_items"][0].items() if k != "due_date_end"}   # รุ่นเก่าไม่มีคีย์นี้
        self.assertEqual(history.diff_reports(report(action_items=[old_style]), report())["action_items"][0]["status"], "same")

    def test_summary_and_other_matters_changes(self):
        d = history.diff_reports(report(), report(summary="สรุปใหม่ทั้งหมด", other_matters="นัดครั้งหน้า"))
        self.assertTrue(d["changed"])
        self.assertEqual(text_of(d["other_matters"], "ins"), "นัดครั้งหน้า")


if __name__ == "__main__":
    unittest.main()
