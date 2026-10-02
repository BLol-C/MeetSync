"""
ทดสอบชั้นบริการ (service.py) แบบครบวงจรกับ MySQL ชั่วคราว: สิทธิ์ -> ตั้งค่าประชุม -> ผู้เข้าร่วม -> ตรวจ transcript ->
AI ร่าง (ใช้ AI ปลอม) -> แก้ -> อนุมัติ -> PDF -> Calendar (ใช้ Calendar ปลอม) ไม่เรียก Gemini/Google จริง
"""

import json
import unittest
import uuid
from unittest import mock

from tests.dbcase import TempDbCase

import calendar_sync  # noqa: E402
import db  # noqa: E402
import service  # noqa: E402
import summarizer  # noqa: E402
from service import ServiceError  # noqa: E402

URL = "https://meet.google.com/abc-defg-hij"


def fake_ai(prompt, schema=None):
    return json.dumps({
        "summary": "ที่ประชุมอนุมัติงบประมาณ",
        "agenda": [{
            "title": "งบประมาณ", "discussion": "นายสมชายเสนอให้อนุมัติงบ", "resolution": "อนุมัติงบสองหมื่นบาท",
            "evidence": ["ผมเสนอให้อนุมัติงบสองหมื่นบาท"],
        }],
        "other_matters": None,
        "action_items": [
            {"description": "ส่งรายงานความก้าวหน้า", "assignee": "Alice", "due_date": "2026-10-09",
             "due_time": "13:00", "due_time_end": None, "evidence": ["จะส่งรายงานความก้าวหน้า"]},
            {"description": "ติดต่อห้องประชุม", "assignee": "ใครสักคน", "due_date": None,
             "due_time": None, "due_time_end": None, "evidence": ["ข้อความที่ไม่มีใน transcript เลย"]},
        ],
    })


class GroupTurnsTests(unittest.TestCase):
    """รวมประโยคต่อเนื่องของคนเดียวกันเป็นช่วงพูด (แสดงผล/ส่งออกเท่านั้น)"""

    @staticmethod
    def seg(sid, name, text, sec, deleted=False, original=None):
        import datetime
        return {"segment_id": sid, "display_name": name, "text": text, "deleted": deleted, "original_text": original,
                "spoken_at": datetime.datetime(2026, 10, 2, 10, 0, 0) + datetime.timedelta(seconds=sec)}

    def test_same_speaker_close_together_merges_and_other_speaker_splits(self):
        turns = service.group_turns([
            self.seg(1, "สมชาย", "ประโยคหนึ่ง", 0), self.seg(2, "สมชาย", "ประโยคสอง", 10),
            self.seg(3, "Alice", "เห็นด้วย", 20), self.seg(4, "สมชาย", "ขอบคุณ", 25),
        ])
        self.assertEqual([t["display_name"] for t in turns], ["สมชาย", "Alice", "สมชาย"])
        self.assertEqual(turns[0]["text"], "ประโยคหนึ่ง ประโยคสอง")
        self.assertEqual(turns[0]["segment_ids"], [1, 2])
        self.assertEqual(service.format_turn_time(turns[0]), "10:00:00–10:00:10")
        self.assertEqual(service.format_turn_time(turns[1]), "10:00:20")          # ประโยคเดียว แสดงเวลาเดียว

    def test_long_pause_starts_a_new_turn_for_the_same_speaker(self):
        turns = service.group_turns([self.seg(1, "สมชาย", "ก", 0), self.seg(2, "สมชาย", "ข", service.TURN_GAP_S),
                                     self.seg(3, "สมชาย", "ค", service.TURN_GAP_S * 3)])
        self.assertEqual([t["text"] for t in turns], ["ก ข", "ค"])                  # ห่างพอดีเกณฑ์ยังรวม เกินแล้วแยก

    def test_deleted_sentences_are_skipped_and_edits_are_flagged(self):
        turns = service.group_turns([
            self.seg(1, "สมชาย", "ดี", 0), self.seg(2, "Bob", "ทดสอบไมค์", 5, deleted=True),
            self.seg(3, "สมชาย", "ต่อ", 8, original="ตอ"),
        ])
        self.assertEqual(len(turns), 1)                                             # ประโยคที่ลบไม่ตัดช่วงพูดของสมชายขาดกลาง
        self.assertEqual(turns[0]["text"], "ดี ต่อ")
        self.assertTrue(turns[0]["edited"])

    def test_empty_input(self):
        self.assertEqual(service.group_turns([]), [])


class FakeCalendar:
    def __init__(self):
        self.inserted = []

    def events(self):
        return self

    def insert(self, calendarId, body, sendUpdates):  # noqa: N803
        self.inserted.append((body, sendUpdates))
        return self

    def execute(self):
        return {"id": f"evt{len(self.inserted)}"}


class ServiceTests(TempDbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.init_schema()
        mk = lambda sub, email, name: {"user_id": db.upsert_user(sub, email, name, None), "email": email, "name": name}  # noqa: E731
        cls.owner = mk("sub-owner", "owner@x.com", "เจ้าของ")
        cls.other = mk("sub-other", "other@x.com", "คนอื่น")
        cls.chair_user = mk("sub-chair", "chair@x.com", "ประธาน")
        cls.alice_user = mk("sub-alice", "alice@x.com", "Alice")

    # ── ตัวช่วย ──

    def new_meeting(self, **over):
        args = dict(meet_url=URL, title="ประชุมโครงการ", org_name="ภาควิชา", meeting_no="3/2569", venue="ห้อง 2",
                    people=[
                        {"display_name": "สมชาย ใจดี", "email": "chair@x.com", "role": "chair", "attendance": "present"},
                        {"display_name": "สมหญิง", "role": "secretary", "attendance": "present"},
                        {"display_name": "Alice", "email": "alice@x.com"},
                        {"display_name": "Bob"},
                    ])
        args.update(over)
        return service.create_meeting(self.owner, **args)

    def record(self, mid):
        """จำลองสิ่งที่บอทบันทึก แล้วจบการประชุม"""
        db.begin_recording(mid)
        s1 = db.get_or_create_speaker(mid, "สมชาย ใจดี (You)")
        s2 = db.get_or_create_speaker(mid, "Alice")
        s3 = db.get_or_create_speaker(mid, "Zed")
        db.insert_segment(mid, s1, 1, "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท")
        db.insert_segment(mid, s2, 2, "เห็นด้วยค่ะ ดิฉันจะส่งรายงานความก้าวหน้าวันศุกร์หน้า")
        db.insert_segment(mid, s3, 3, "ทดสอบไมค์ ๆ")
        db.end_meeting(mid)

    def verified(self):
        mid = self.new_meeting()
        self.record(mid)
        zed = next(s for s in service.list_segments(self.owner, mid) if s["display_name"] == "Zed")
        service.apply_segments_table(self.owner, mid, [{"segment_id": zed["segment_id"], "display_name": "Zed",
                                                         "text": zed["text"], "deleted": True}])
        service.verify_transcript(self.owner, mid)
        return mid

    def draft(self):
        mid = self.verified()
        service.generate_report(self.owner, mid, generate=fake_ai)
        return mid

    # ── สิทธิ์ ──

    def test_other_users_cannot_see_or_touch_a_meeting(self):
        mid = self.new_meeting()
        self.record(mid)
        calls = [
            lambda: service.get_detail(self.other, mid), lambda: service.list_segments(self.other, mid),
            lambda: service.transcript_text(self.other, mid), lambda: service.verify_transcript(self.other, mid),
            lambda: service.generate_report(self.other, mid, generate=fake_ai), lambda: service.build_pdf(self.other, mid),
            lambda: service.add_person(self.other, mid, "x"), lambda: service.update_meeting(self.other, mid, title="แฮ็ก"),
            lambda: service.delete_meeting(self.other, mid), lambda: service.get_report_view(self.other, mid),
            lambda: service.approve_report(self.other, mid), lambda: service.reopen_report(self.other, mid),
        ]
        for call in calls:
            with self.assertRaises(ServiceError) as cm:
                call()
            self.assertEqual(cm.exception.kind, "not_found")
        self.assertEqual(service.list_meetings(self.other), [])

    def test_chair_by_email_can_manage_but_plain_attendee_cannot(self):
        mid = self.new_meeting()
        self.assertEqual(service.get_detail(self.chair_user, mid)["meeting"]["meeting_id"], mid)
        self.assertIn(mid, [m["meeting_id"] for m in service.list_meetings(self.chair_user)])
        with self.assertRaises(ServiceError):
            service.get_detail(self.alice_user, mid)            # ผู้เข้าร่วมธรรมดา ไม่ใช่ประธาน/เลขา

    # ── สร้าง/แก้การประชุม ──

    def test_create_meeting_validation_is_atomic(self):
        before = len(service.list_meetings(self.owner))
        bad_cases = [
            dict(meet_url="https://example.com/x"),
            dict(people=[{"display_name": "A", "role": "chair"}, {"display_name": "B", "role": "chair"}]),
            dict(people=[{"display_name": "A"}, {"display_name": "a"}]),                      # ชื่อซ้ำ (ไม่สนตัวพิมพ์)
            dict(people=[{"display_name": "A"}, {"display_name": "B", "role": "boss"}]),     # บทบาทผิดที่รายการท้าย
        ]
        for over in bad_cases:
            with self.assertRaises(ServiceError):
                self.new_meeting(**over)
        self.assertEqual(len(service.list_meetings(self.owner)), before)     # ไม่เหลือการประชุมครึ่งๆ กลางๆ

    def test_create_meeting_defaults_and_header(self):
        mid = self.new_meeting(title="  ", people=[])
        detail = service.get_detail(self.owner, mid)
        self.assertIn("การประชุม", detail["meeting"]["title"])               # ไม่ใส่ชื่อ = ตั้งชื่อให้จากวันเวลา
        self.assertEqual(detail["meeting"]["status"], "scheduled")
        mid2 = self.new_meeting()
        d = service.get_detail(self.owner, mid2)
        self.assertEqual((d["header"]["chair"], d["header"]["secretary"]), ("สมชาย ใจดี", "สมหญิง"))
        self.assertEqual([a["name"] for a in d["header"]["attendees"]], ["สมชาย ใจดี", "สมหญิง"])
        self.assertEqual({a["name"] for a in d["header"]["absent"]}, {"Alice", "Bob"})      # ยังไม่ยืนยัน = ไม่มา
        self.assertTrue(any(w["code"] == "unconfirmed_attendance" for w in d["header_warnings"]))

    def test_meetings_with_same_link_are_reported_before_creating_a_duplicate(self):
        mid = self.new_meeting(meet_url="https://meet.google.com/zzz-zzzz-zzz?authuser=0")
        found = service.meetings_with_url(self.owner, "https://meet.google.com/ZZZ-zzzz-zzz")
        self.assertEqual([m["meeting_id"] for m in found], [mid])
        self.assertEqual(service.meetings_with_url(self.owner, ""), [])
        self.assertEqual(service.meetings_with_url(self.other, "https://meet.google.com/zzz-zzzz-zzz"), [])

    def test_update_and_delete_rules(self):
        mid = self.new_meeting()
        service.update_meeting(self.owner, mid, title="ชื่อใหม่", venue=None)
        self.assertEqual(service.get_detail(self.owner, mid)["meeting"]["title"], "ชื่อใหม่")
        with self.assertRaises(ServiceError):
            service.update_meeting(self.owner, mid, meet_url="https://bad.example")
        service.delete_meeting(self.owner, mid)                                  # ยังไม่เริ่ม ลบได้
        with self.assertRaises(ServiceError):
            service.get_detail(self.owner, mid)
        started = self.new_meeting()
        db.begin_recording(started)
        with self.assertRaises(ServiceError) as cm:
            service.delete_meeting(self.owner, started)
        self.assertEqual(cm.exception.kind, "conflict")
        with self.assertRaises(ServiceError):
            service.update_meeting(self.owner, started, meet_url=URL)            # เริ่มแล้วเปลี่ยนลิงก์ไม่ได้

    # ── ผู้เข้าร่วม ──

    def table_rows(self, mid):
        return [{k: p[k] for k in ("speaker_id", "display_name", "email", "role", "attendance")}
                for p in service.get_detail(self.owner, mid)["people"]]

    def test_people_table_add_update_delete_and_swap_chair(self):
        mid = self.new_meeting()
        rows = self.table_rows(mid)
        by = {r["display_name"]: r for r in rows}
        by["Alice"]["role"], by["สมชาย ใจดี"]["role"] = "chair", "attendee"          # สลับประธานในการบันทึกครั้งเดียว
        by["Alice"]["attendance"] = "present"
        by["Bob"]["email"] = "bob@x.com"
        rows = [r for r in rows if r["display_name"] != "สมหญิง"]                      # ลบเลขา
        rows.append({"speaker_id": None, "display_name": "Carol", "email": "", "role": "secretary", "attendance": "present"})
        rows.append({"speaker_id": float("nan"), "display_name": "Dave", "email": "d@x.com", "role": "attendee", "attendance": "invited"})
        rows.append({"speaker_id": None, "display_name": "   ", "email": "", "role": "attendee", "attendance": "invited"})   # แถวว่างข้าม
        result = service.apply_people_table(self.owner, mid, rows)
        self.assertEqual(result["errors"], [], result)
        self.assertEqual((result["updated"], result["added"], result["deleted"]), (3, 2, 1))
        d = service.get_detail(self.owner, mid)
        self.assertEqual((d["header"]["chair"], d["header"]["secretary"]), ("Alice", "Carol"))
        self.assertEqual({p["display_name"]: p["email"] for p in d["people"]}["Bob"], "bob@x.com")
        self.assertEqual(sorted(p["display_name"] for p in d["people"]), ["Alice", "Bob", "Carol", "Dave", "สมชาย ใจดี"])

    def test_people_table_reports_row_errors_without_losing_the_rest(self):
        mid = self.new_meeting()
        rows = self.table_rows(mid)
        by = {r["display_name"]: r for r in rows}
        by["Bob"]["role"] = "chair"                       # มีประธานแล้ว (สมชาย)
        by["Alice"]["attendance"] = "present"             # แถวอื่นยังต้องบันทึกได้
        rows.append({"speaker_id": None, "display_name": "Alice", "email": "", "role": "attendee", "attendance": "invited"})   # ชื่อซ้ำ
        result = service.apply_people_table(self.owner, mid, rows)
        self.assertEqual(len(result["errors"]), 2)
        self.assertTrue(any("มีประธานแล้ว" in e for e in result["errors"]))
        self.assertTrue(any("มีผู้เข้าร่วมชื่อ" in e for e in result["errors"]))
        self.assertEqual(next(p for p in service.get_detail(self.owner, mid)["people"] if p["display_name"] == "Alice")["attendance"], "present")

    def test_people_with_transcript_cannot_be_deleted_by_removing_the_row(self):
        mid = self.new_meeting()
        self.record(mid)
        rows = [r for r in self.table_rows(mid) if r["display_name"] != "Alice"]      # Alice มีข้อความแล้ว
        result = service.apply_people_table(self.owner, mid, rows)
        self.assertEqual(result["deleted"], 0)
        self.assertTrue(any("Alice" in e and "มีข้อความ" in e for e in result["errors"]))

    # ── transcript ──

    def test_speaker_names_from_meet_map_to_registered_people_automatically(self):
        mid = self.new_meeting()
        self.record(mid)
        people = {p["display_name"]: p for p in service.get_detail(self.owner, mid)["people"]}
        self.assertEqual(people["สมชาย ใจดี"]["segment_count"], 1)                   # "(You)" ถูกตัด ไม่เกิดคนซ้ำ
        self.assertEqual(people["Alice"]["attendance"], "present")                   # พูดแล้ว = เข้าร่วม
        self.assertEqual((people["Zed"]["source"], people["Zed"]["attendance"]), ("meet", "present"))
        self.assertEqual(len(people), 5)

    def test_merge_people_fixes_a_name_mismatch(self):
        mid = self.new_meeting()
        db.begin_recording(mid)
        s = db.get_or_create_speaker(mid, "46 Bob Smith")
        db.insert_segment(mid, s, 1, "สวัสดี")
        db.end_meeting(mid)
        people = {p["display_name"]: p for p in service.get_detail(self.owner, mid)["people"]}
        bob = people["Bob"]["speaker_id"]
        self.assertEqual(service.merge_people(self.owner, s, bob), 1)
        d = service.get_detail(self.owner, mid)
        self.assertNotIn("46 Bob Smith", [p["display_name"] for p in d["people"]])
        self.assertEqual({p["display_name"]: p["segment_count"] for p in d["people"]}["Bob"], 1)
        with self.assertRaises(ServiceError):
            service.merge_people(self.owner, bob, bob)

    def test_transcript_review_gate(self):
        mid = self.new_meeting()
        self.record(mid)
        with self.assertRaises(ServiceError) as cm:                              # ยังไม่ยืนยัน -> AI สร้างรายงานไม่ได้
            service.generate_report(self.owner, mid, generate=fake_ai)
        self.assertEqual(cm.exception.kind, "conflict")
        self.assertIn("ยืนยัน transcript", str(cm.exception))

        segs = service.list_segments(self.owner, mid)
        rows = [{"segment_id": s["segment_id"], "display_name": s["display_name"], "text": s["text"], "deleted": s["deleted"]} for s in segs]
        rows[0]["text"] = rows[0]["text"] + " ครับ"
        rows[2]["deleted"] = True
        rows[1]["display_name"] = "Bob"                                          # ย้ายผู้พูดไปคนที่ลงทะเบียนไว้
        result = service.apply_segments_table(self.owner, mid, rows)
        self.assertEqual((result["changed"], result["errors"]), (3, []))
        again = service.apply_segments_table(self.owner, mid, rows)             # ไม่มีอะไรเปลี่ยน
        self.assertEqual(again["changed"], 0)
        segs = service.list_segments(self.owner, mid)
        self.assertEqual(segs[0]["original_text"], "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท")   # ต้นฉบับยังอยู่
        self.assertEqual(segs[1]["display_name"], "Bob")
        text = service.transcript_text(self.owner, mid)
        self.assertIn("ครับ", text)
        self.assertNotIn("ทดสอบไมค์", text)                                      # ช่วงที่ลบไม่ถูกส่งต่อให้ AI/ไฟล์

        rows[0]["text"] = "   "
        self.assertEqual(len(service.apply_segments_table(self.owner, mid, rows)["errors"]), 1)   # ข้อความว่างไม่ได้
        with self.assertRaises(ServiceError):
            service.verify_transcript(self.owner, mid, bot_running=True)         # บอทยังรันอยู่
        v = service.verify_transcript(self.owner, mid)
        self.assertEqual(v["unmapped"], [])                                      # Zed มีแต่ช่วงที่ลบ -> ไม่นับ
        with self.assertRaises(ServiceError) as cm:                              # ยืนยันแล้วแก้ไม่ได้ ต้อง reopen
            service.apply_segments_table(self.owner, mid, rows)
        self.assertEqual(cm.exception.kind, "conflict")
        service.reopen_transcript(self.owner, mid)
        self.assertEqual(service.apply_segments_table(self.owner, mid, rows)["errors"] != [], True)

    def test_verify_lists_unmapped_meet_names(self):
        mid = self.new_meeting()
        self.record(mid)
        self.assertEqual(service.verify_transcript(self.owner, mid)["unmapped"], ["Zed"])

    # ── รายงาน ──

    def test_generate_flags_unreliable_ai_output(self):
        mid = self.verified()
        out = service.generate_report(self.owner, mid, generate=fake_ai)
        codes = [w["code"] for w in out["warnings"]]
        self.assertIn("assignee_unknown", codes)             # "ใครสักคน" ไม่อยู่ในรายชื่อ
        self.assertIn("ungrounded", codes)                   # หลักฐานที่ไม่มีใน transcript
        view = service.get_report_view(self.owner, mid)
        self.assertEqual(view["meeting"]["status"], "draft")
        self.assertTrue(view["editable"])
        self.assertIs(view["report"]["content"]["agenda"][0]["grounded"], True)
        item = view["report"]["content"]["action_items"][0]
        self.assertIn("action_item_id", item)                # แถวงานจริงในฐานข้อมูล (ไม่ใช่ JSON)
        self.assertFalse(item["calendar_synced"])

    def test_regenerating_replaces_the_draft_instead_of_stacking(self):
        mid = self.draft()
        service.generate_report(self.owner, mid, generate=fake_ai)
        service.generate_report(self.owner, mid, generate=fake_ai)
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM summaries WHERE meeting_id=%s", (mid,))[0]["n"], 1)   # รายงาน 1 ฉบับต่อ 1 ประชุม

    def test_ai_failures_are_reported_in_plain_language(self):
        mid = self.verified()

        def boom(prompt, schema=None):
            raise ConnectionError("503 UNAVAILABLE")
        with self.assertRaises(ServiceError) as cm:
            service.generate_report(self.owner, mid, generate=boom)
        self.assertEqual(cm.exception.kind, "unavailable")
        self.assertIn("Gemini", str(cm.exception))
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")   # พลาดแล้วสถานะไม่เปลี่ยน ลองใหม่ได้

    def test_edit_then_approve_requires_confirmation_then_locks(self):
        mid = self.draft()
        view = service.get_report_view(self.owner, mid)
        content = view["report"]["content"]
        content["action_items"][1].update(assignee="Bob", due_date="2026-10-12")
        content["agenda"][0]["resolution"] = "ที่ประชุมอนุมัติงบประมาณ 20,000 บาท"
        saved = service.save_report_draft(self.owner, mid, content)
        self.assertNotIn("assignee_unknown", [w["code"] for w in saved["warnings"]])

        with self.assertRaises(ServiceError) as cm:                              # ยังมีคำเตือน -> ต้องยืนยัน
            service.approve_report(self.owner, mid)
        self.assertEqual(cm.exception.kind, "needs_confirmation")
        self.assertTrue(cm.exception.extra["warnings"])
        self.assertEqual(db.get_meeting(mid)["status"], "draft")

        service.approve_report(self.owner, mid, confirm_warnings=True)
        view = service.get_report_view(self.owner, mid)
        self.assertEqual(view["meeting"]["status"], "approved")
        self.assertTrue(view["report"]["approved"])
        self.assertEqual(view["report"]["approved_by_name"], "เจ้าของ")
        self.assertFalse(view["editable"])
        self.assertEqual(view["report"]["content"]["agenda"][0]["resolution"], "ที่ประชุมอนุมัติงบประมาณ 20,000 บาท")

        # อนุมัติแล้วทุกอย่างถูกล็อก
        locked = [
            lambda: service.save_report_draft(self.owner, mid, content), lambda: service.generate_report(self.owner, mid, generate=fake_ai),
            lambda: service.update_meeting(self.owner, mid, title="แก้"), lambda: service.add_person(self.owner, mid, "x"),
            lambda: service.apply_people_table(self.owner, mid, []), lambda: service.apply_segments_table(self.owner, mid, []),
            lambda: service.approve_report(self.owner, mid, confirm_warnings=True), lambda: service.reopen_transcript(self.owner, mid),
        ]
        for call in locked:
            with self.assertRaises(ServiceError) as cm:
                call()
            self.assertEqual(cm.exception.kind, "conflict")

    def test_clean_report_can_be_approved_without_confirmation(self):
        mid = self.new_meeting(people=[
            {"display_name": "สมชาย ใจดี", "role": "chair", "attendance": "present"},
            {"display_name": "สมหญิง", "role": "secretary", "attendance": "present"},
            {"display_name": "Alice", "attendance": "present"}])
        self.record(mid)
        zed = next(s for s in service.list_segments(self.owner, mid) if s["display_name"] == "Zed")
        db.edit_segment(zed["segment_id"], deleted=True)                         # ชื่อที่ไม่รู้จักถูกจัดการแล้ว
        service.verify_transcript(self.owner, mid)

        def clean_ai(prompt, schema=None):
            body = json.loads(fake_ai(prompt))
            body["action_items"] = body["action_items"][:1]
            return json.dumps(body)
        service.generate_report(self.owner, mid, generate=clean_ai)
        self.assertEqual(service.approval_warnings(self.owner, mid), [], service.approval_warnings(self.owner, mid))
        service.approve_report(self.owner, mid)           # ไม่มีอะไรต้องยืนยัน

    def test_pdf_watermark_until_approved(self):
        mid = self.draft()
        draft, name = service.build_pdf(self.owner, mid)
        self.assertTrue(draft.startswith(b"%PDF"))
        self.assertIn("draft", name)
        service.approve_report(self.owner, mid, confirm_warnings=True)
        final, name2 = service.build_pdf(self.owner, mid)
        self.assertNotIn("draft", name2)
        self.assertNotEqual(draft, final)
        with self.assertRaises(ServiceError):
            service.build_pdf(self.owner, self.new_meeting())                    # ยังไม่มีรายงาน

    def test_reopen_lets_an_approved_report_be_fixed_and_approved_again(self):
        mid = self.draft()
        with self.assertRaises(ServiceError):
            service.reopen_report(self.owner, mid)                               # ยังไม่อนุมัติ
        service.approve_report(self.owner, mid, confirm_warnings=True)
        service.reopen_report(self.owner, mid)
        view = service.get_report_view(self.owner, mid)
        self.assertEqual((view["meeting"]["status"], view["report"]["approved"], view["editable"]), ("draft", False, True))
        content = view["report"]["content"]
        content["summary"] = "สรุปที่แก้หลังยกเลิกการอนุมัติ"
        service.save_report_draft(self.owner, mid, content)
        service.approve_report(self.owner, mid, confirm_warnings=True)
        final = service.get_report_view(self.owner, mid)
        self.assertEqual((final["report"]["approved"], final["report"]["content"]["summary"]), (True, "สรุปที่แก้หลังยกเลิกการอนุมัติ"))
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM summaries WHERE meeting_id=%s", (mid,))[0]["n"], 1)

    # ── Calendar ──

    def test_calendar_sync_only_after_approval_with_attendees(self):
        mid = self.draft()
        fake = FakeCalendar()
        content = service.get_report_view(self.owner, mid)["report"]["content"]
        content["action_items"][1].update(assignee="Bob", due_date="2026-10-12")
        service.save_report_draft(self.owner, mid, content)
        first = service.get_report_view(self.owner, mid)["report"]["content"]["action_items"][0]["action_item_id"]
        with self.assertRaises(ServiceError) as cm:
            service.sync_all(self.owner, mid)                                    # ยังไม่อนุมัติ
        self.assertEqual(cm.exception.kind, "conflict")
        with self.assertRaises(ServiceError):
            service.sync_action_item(self.owner, first)
        service.approve_report(self.owner, mid, confirm_warnings=True)

        with mock.patch.object(calendar_sync, "build", lambda *a, **k: fake), \
             mock.patch.object(calendar_sync.calendar_auth, "get_credentials", lambda uid: object()):
            results = service.sync_all(self.owner, mid)
            self.assertTrue(all(r["ok"] for r in results), results)
            self.assertEqual(len(fake.inserted), 2)
            alice_ev = next(b for b, _ in fake.inserted if b["summary"] == "ส่งรายงานความก้าวหน้า")
            self.assertEqual(alice_ev["attendees"], [{"email": "alice@x.com"}])
            self.assertEqual(alice_ev["start"]["dateTime"], "2026-10-09T13:00:00")
            self.assertEqual({s for _, s in fake.inserted}, {"none"})            # ค่าเริ่มต้นไม่ส่งอีเมลเชิญจริง
            again = service.sync_all(self.owner, mid)                           # ส่งซ้ำไม่สร้าง event ซ้ำ
            self.assertTrue(all(r.get("already") for r in again))
            self.assertEqual(len(fake.inserted), 2)
            with self.assertRaises(ServiceError) as cm:                          # คนอื่นแตะงานนี้ไม่ได้
                service.sync_action_item(self.other, first)
            self.assertEqual(cm.exception.kind, "not_found")

    def test_calendar_item_without_a_date_is_skipped_not_guessed(self):
        mid = self.draft()                                                       # งานที่สองไม่มีวันที่
        service.approve_report(self.owner, mid, confirm_warnings=True)
        fake = FakeCalendar()
        with mock.patch.object(calendar_sync, "build", lambda *a, **k: fake), \
             mock.patch.object(calendar_sync.calendar_auth, "get_credentials", lambda uid: object()):
            results = service.sync_all(self.owner, mid)
        self.assertEqual([r["ok"] for r in results], [True])                     # ข้ามเฉย ๆ ไม่นับเป็นความล้มเหลว
        self.assertEqual(len(fake.inserted), 1)                                  # ไม่เดาวันให้งานที่ไม่มีวัน
        self.assertEqual(fake.inserted[0][0]["summary"].count("ติดต่อห้องประชุม"), 0)

    def test_calendar_not_connected_is_a_readable_error(self):
        mid = self.draft()
        service.approve_report(self.owner, mid, confirm_warnings=True)
        results = service.sync_all(self.owner, mid)
        self.assertTrue(all(not r["ok"] and "ยังไม่เชื่อมต่อ" in r["error"] for r in results))

    def test_calendar_token_is_per_user(self):
        db.save_calendar_token(self.owner["user_id"], '{"a": 1}')
        self.assertTrue(service.calendar_connected(self.owner))
        self.assertFalse(service.calendar_connected(self.other))


if __name__ == "__main__":
    unittest.main()
