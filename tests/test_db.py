"""
ทดสอบชั้นข้อมูล (db.py) กับ MySQL จริงในฐานข้อมูลชั่วคราว meetsync_test_<สุ่ม> — สร้างและลบเองทุกครั้ง
ไม่แตะฐานข้อมูลจริงของแอป (DB_NAME ใน .env) ถ้าต่อ MySQL ไม่ได้ ทุกเทสต์จะถูกข้าม (skip)

รัน:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import unittest
import uuid

from tests.dbcase import TempDbCase   # ต้อง import ก่อน db: โหลด .env ให้เรียบร้อยก่อน

import db  # noqa: E402

URL = "https://meet.google.com/abc-defg-hij"


class SchemaTests(TempDbCase):
    """ไม่มีระบบอัปเกรดโครงสร้าง: ฐานเปล่าได้ครบทุกตาราง, เรียกซ้ำได้, และฐานโครงสร้างเก่าต้องแจ้ง error ชัดเจน"""

    per_test = True   # แต่ละเคสต้องเริ่มจากฐานเปล่า

    def tables(self):
        return {r["TABLE_NAME"] for r in self.rows(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")}

    def test_empty_database_gets_every_table_and_no_migration_table(self):
        db.init_schema()
        self.assertEqual(self.tables(), {"users", "meetings", "speakers", "transcript_segments", "summaries",
                                         "agenda_items", "action_items"})

    def test_init_schema_twice_keeps_data(self):
        db.init_schema()
        uid = db.upsert_user("sub-twice", "t@x.com", "T", None)
        mid = db.create_meeting_setup(uid, meet_url=URL)
        db.init_schema()
        self.assertEqual(db.get_meeting(mid)["meeting_id"], mid)

    def test_two_processes_starting_at_once_create_tables_once(self):
        """หน้าเว็บกับบริการบอทเรียก init_schema() ตอนเริ่มพร้อมกัน — ต้องไม่ error จากการสร้างตารางซ้ำ"""
        import threading

        errors = []
        barrier = threading.Barrier(4)

        def run():
            try:
                barrier.wait()
                db.init_schema()
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.tables()), 7)

    def test_database_with_an_older_structure_is_reported_clearly(self):
        db.init_schema()
        self.execute("ALTER TABLE speakers DROP COLUMN absence_reason")
        with self.assertRaises(RuntimeError) as ctx:
            db.init_schema()
        self.assertIn("speakers.absence_reason", str(ctx.exception))
        self.assertIn("DROP DATABASE", str(ctx.exception))

    def test_expected_columns_cover_all_tables_in_the_schema(self):
        cols = db._expected_columns()
        self.assertEqual(set(cols), {"users", "meetings", "speakers", "transcript_segments", "summaries",
                                     "agenda_items", "action_items"})
        self.assertIn("absence_reason", cols["speakers"])
        self.assertNotIn("PRIMARY", cols["users"])


class WorkflowTests(TempDbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.init_schema()

    def new_meeting(self, **fields):
        uid = db.upsert_user(f"sub-{uuid.uuid4().hex[:6]}", "a@b.com", "A", None)
        return uid, db.create_meeting_setup(uid, meet_url=URL, **fields)

    def started(self):
        uid, mid = self.new_meeting()
        db.begin_recording(mid)
        return uid, mid

    # ── สถานะ ──

    def test_mark_unconfirmed_absent_only_touches_invited_people_of_that_meeting(self):
        uid, mid = self.new_meeting()
        _, other = self.new_meeting()
        a = db.add_speaker(mid, "A", attendance="invited")
        b = db.add_speaker(mid, "B", attendance="present")
        c = db.add_speaker(mid, "C", attendance="absent", absence_reason="ลา")
        d = db.add_speaker(mid, "D")                                   # ค่าเริ่มต้น invited
        x = db.add_speaker(other, "X", attendance="invited")           # อีกการประชุมต้องไม่ถูกแตะ
        self.assertEqual(db.mark_unconfirmed_absent(mid), ["A", "D"])
        got = {s: db.get_speaker(s)["attendance"] for s in (a, b, c, d, x)}
        self.assertEqual(got, {a: "absent", b: "present", c: "absent", d: "absent", x: "invited"})
        self.assertEqual(db.get_speaker(c)["absence_reason"], "ลา")
        self.assertEqual(db.mark_unconfirmed_absent(mid), [])         # เรียกซ้ำไม่เปลี่ยนอะไร

    def test_deleting_the_previous_meeting_clears_the_link_and_sections_roundtrip(self):
        uid, prev = self.new_meeting()
        cur = db.create_meeting_setup(uid, meet_url=URL, previous_meeting_id=prev)
        self.assertEqual(db.get_meeting(cur)["previous_meeting_id"], prev)
        db.update_meeting_setup(cur, previous_meeting_id=None)
        self.assertIsNone(db.get_meeting(cur)["previous_meeting_id"])
        db.update_meeting_setup(cur, previous_meeting_id=prev)
        self.assertTrue(db.delete_scheduled_meeting(prev))             # ตัวที่ถูกอ้างอิงถูกลบ -> ลิงก์ว่าง ไม่ทำให้ลบไม่ได้
        self.assertIsNone(db.get_meeting(cur)["previous_meeting_id"])
        db.begin_recording(cur)
        db.end_meeting(cur)
        content = {"summary": "s", "other_matters": None, "action_items": [], "agenda": [
            {"section": "followup", "title": "ก", "discussion": "", "resolution": None, "evidence": []},
            {"title": "ไม่ระบุหมวด", "discussion": "", "resolution": None, "evidence": []},
            {"section": "nonsense", "title": "หมวดผิด", "discussion": "", "resolution": None, "evidence": []}]}
        db.save_report(cur, content)
        self.assertEqual([(a["title"], a["section"]) for a in db.get_report(cur)["content"]["agenda"]],
                         [("ก", "followup"), ("ไม่ระบุหมวด", "consider_new"), ("หมวดผิด", "consider_new")])

    def test_mark_present_by_names_only_changes_registered_invited_people(self):
        uid, mid = self.new_meeting()
        a = db.add_speaker(mid, "ธนาวีร์ บุญเกิด")
        db.update_speaker(a, meet_alias="46 ธนาวีร์ บุญเกิด")
        b = db.add_speaker(mid, "Bob")
        c = db.add_speaker(mid, "Carol", attendance="absent", absence_reason="ลา")
        d = db.add_speaker(mid, "Dan", attendance="present")
        before = {r["display_name"] for r in db.list_speakers(mid)}
        changed = db.mark_present_by_names(mid, ["46 ธนาวีร์ บุญเกิด (You)", "bob", "Carol", "Dan", "คนไม่รู้จัก", ""])
        self.assertEqual(sorted(changed), sorted(["ธนาวีร์ บุญเกิด", "Bob"]))
        got = {s: db.get_speaker(s)["attendance"] for s in (a, b, c, d)}
        self.assertEqual(got, {a: "present", b: "present", c: "absent", d: "present"})   # ที่ตั้ง "ไม่มา" ไว้เองไม่ถูกแตะ
        self.assertEqual({r["display_name"] for r in db.list_speakers(mid)}, before)       # ไม่สร้างผู้เข้าร่วมใหม่
        self.assertEqual(db.mark_present_by_names(mid, ["bob"]), [])                       # เรียกซ้ำไม่เปลี่ยนอะไร
        self.assertEqual(db.mark_present_by_names(mid, []), [])

    def test_unknown_room_names_are_kept_as_meet_guests_so_they_can_be_matched_later(self):
        uid, mid = self.new_meeting()
        a = db.add_speaker(mid, "ธนาวีร์")
        out = db.record_room_names(mid, ["Thanawee BOONKERD", "  ", "Thanawee BOONKERD", "ธนาวีร์"])
        self.assertEqual(out, {"marked": ["ธนาวีร์"], "new": ["Thanawee BOONKERD"]})
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        room = people["Thanawee BOONKERD"]
        self.assertEqual((room["source"], room["role"], room["attendance"], room["segment_count"]),
                         ("meet", "guest", "present", 0))
        self.assertEqual(db.record_room_names(mid, ["Thanawee BOONKERD"]), {"marked": [], "new": []})   # ไม่สร้างซ้ำ
        db.merge_speakers(room["speaker_id"], a)                      # ผู้ใช้จับคู่ว่าเป็นคนเดียวกับ "ธนาวีร์"
        self.assertEqual(db.get_speaker(a)["meet_alias"], "Thanawee BOONKERD")
        self.assertEqual(db.record_room_names(mid, ["Thanawee BOONKERD"]), {"marked": [], "new": []})   # จำชื่อไว้ ไม่สร้างใหม่
        self.assertEqual([p["display_name"] for p in db.list_speakers(mid)], ["ธนาวีร์"])

    def test_setup_then_record_then_end(self):
        uid, mid = self.new_meeting(title="  ประชุม  ", venue="", meeting_no="3/2569")
        m = db.get_meeting(mid)
        self.assertEqual((m["status"], m["title"], m["venue"], m["meeting_no"]), ("scheduled", "ประชุม", None, "3/2569"))
        self.assertTrue(db.begin_recording(mid))
        self.assertEqual(db.get_meeting(mid)["status"], "recording")
        db.end_meeting(mid)
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_review")
        self.assertIsNotNone(db.get_meeting(mid)["ended_at"])
        self.assertTrue(db.begin_recording(mid))                 # บอทหลุด -> บันทึกต่อได้
        self.assertEqual(db.get_meeting(mid)["status"], "recording")
        self.assertIsNone(db.get_meeting(mid)["ended_at"])
        db.end_meeting(mid)
        db.set_meeting_status(mid, "transcript_verified")
        db.end_meeting(mid)                                      # เรียกซ้ำต้องไม่ดึงสถานะถอยหลัง
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")
        self.assertFalse(db.begin_recording(mid))                # ผ่านขั้นตรวจแล้วบันทึกต่อไม่ได้

    def test_setup_validation_and_scheduled_delete(self):
        uid = db.upsert_user("sub-v", "v@x.com", "V", None)
        with self.assertRaises(ValueError):
            db.create_meeting_setup(uid, meet_url="  ")
        with self.assertRaises(ValueError):
            db.create_meeting_setup(uid, meet_url=URL, nonsense="x")
        mid = db.create_meeting_setup(uid, meet_url=URL)
        db.add_speaker(mid, "A")
        db.update_meeting_setup(mid, title="ใหม่", venue=None)
        self.assertEqual(db.get_meeting(mid)["title"], "ใหม่")
        self.assertTrue(db.delete_scheduled_meeting(mid))
        self.assertEqual(db.list_speakers(mid), [])              # ลบพร้อมรายชื่อ
        mid2 = db.create_meeting_setup(uid, meet_url=URL)
        db.begin_recording(mid2)
        self.assertFalse(db.delete_scheduled_meeting(mid2))      # เริ่มแล้วลบไม่ได้

    def test_status_transitions(self):
        _, mid = self.new_meeting()
        self.assertFalse(db.set_meeting_status(mid, "transcript_review"))     # scheduled -> review ข้ามขั้นไม่ได้
        db.begin_recording(mid)
        self.assertFalse(db.set_meeting_status(mid, "draft"))
        self.assertFalse(db.set_meeting_status(mid, "approved"))
        db.end_meeting(mid)
        self.assertFalse(db.set_meeting_status(mid, "draft"))                 # ยังไม่ verify
        self.assertTrue(db.set_meeting_status(mid, "transcript_verified"))
        self.assertFalse(db.set_meeting_status(mid, "approved"))
        self.assertTrue(db.set_meeting_status(mid, "draft"))
        self.assertTrue(db.set_meeting_status(mid, "draft"))                  # สร้างรายงานใหม่ซ้ำได้
        self.assertTrue(db.set_meeting_status(mid, "transcript_review"))      # กลับไปแก้ transcript ได้
        self.assertTrue(db.set_meeting_status(mid, "transcript_verified"))
        self.assertTrue(db.set_meeting_status(mid, "draft"))
        uid = db.upsert_user("sub-ap", "ap@x.com", "AP", None)
        db.save_report(mid, {"summary": "x", "agenda": [], "action_items": []})
        self.assertTrue(db.approve_report(mid, uid))
        for s in ("scheduled", "recording", "transcript_review", "transcript_verified", "draft", "approved"):
            self.assertFalse(db.set_meeting_status(mid, s), f"approved แล้วต้องล็อก ({s})")
        with self.assertRaises(ValueError):
            db.set_meeting_status(mid, "nonsense")

    # ── ผู้เข้าร่วม ──

    def test_roles_chair_and_secretary_are_unique(self):
        _, mid = self.new_meeting()
        chair = db.add_speaker(mid, "ประธาน ก", role="chair")
        db.add_speaker(mid, "เลขา ข", role="secretary")
        with self.assertRaisesRegex(ValueError, "มีประธานแล้ว"):
            db.add_speaker(mid, "คนอื่น", role="chair")
        member = db.add_speaker(mid, "สมาชิก ค")
        with self.assertRaisesRegex(ValueError, "มีเลขาแล้ว"):
            db.update_speaker(member, role="secretary")
        db.update_speaker(chair, role="attendee")                # ปลดประธานเดิมแล้วตั้งคนใหม่ได้
        db.update_speaker(member, role="chair")
        db.update_speaker(member, role="chair", display_name="สมาชิก ค (ประธาน)")   # ตัวเองถือบทบาทอยู่แล้วต้องไม่ชนตัวเอง
        self.assertEqual([s["role"] for s in db.list_speakers(mid)][:2], ["chair", "secretary"])   # เรียงประธาน เลขา ก่อน

    def test_speaker_validation(self):
        _, mid = self.new_meeting()
        for kwargs in ({"display_name": "  "}, {"display_name": "x", "role": "boss"}, {"display_name": "x", "attendance": "maybe"}):
            with self.assertRaises(ValueError):
                db.add_speaker(mid, **kwargs)
        sid = db.add_speaker(mid, " ทดสอบ ", email="  ")
        p = db.get_speaker(sid)
        self.assertEqual((p["display_name"], p["email"], p["role"], p["attendance"], p["source"]),
                         ("ทดสอบ", None, "attendee", "invited", "registered"))
        with self.assertRaisesRegex(ValueError, "มีผู้เข้าร่วมชื่อ"):
            db.add_speaker(mid, "ทดสอบ")
        with self.assertRaises(ValueError):
            db.update_speaker(sid, meeting_id=999)               # ฟิลด์ที่ไม่อนุญาตให้แก้
        db.update_speaker(sid, attendance="absent", email="t@x.com")
        self.assertEqual(db.get_speaker(sid)["attendance"], "absent")
        db.delete_speaker(sid)
        self.assertIsNone(db.get_speaker(sid))

    def test_meet_name_maps_to_registered_person_without_creating_duplicates(self):
        _, mid = self.new_meeting()
        somchai = db.add_speaker(mid, "สมชาย  ใจดี")
        alice = db.add_speaker(mid, "Alice")
        db.add_speaker(mid, "Dan A")
        db.add_speaker(mid, "Dan B")
        got = {n: db.get_or_create_speaker(mid, n) for n in ("Alice (You)", "สมชาย ใจดี (คุณ)", "alice", "Dan", "Bob")}
        self.assertEqual(got["Alice (You)"], alice)                       # (You) ถูกตัด
        self.assertEqual(got["alice"], alice)                             # ตัวพิมพ์ต่างกัน คนเดียวกัน
        self.assertEqual(got["สมชาย ใจดี (คุณ)"], somchai)                 # ช่องว่าง/(คุณ) ไม่ทำให้เป็นคนใหม่
        self.assertNotIn(got["Dan"], {alice, somchai})                    # "Dan" ตรงสองคนที่ไม่ใช่ชื่อเต็ม -> ไม่เดา สร้างผู้พูดใหม่
        names = [s["display_name"] for s in db.list_speakers(mid)]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), 6)                                   # 4 ลงทะเบียน + "Dan" + "Bob"
        by = {s["display_name"]: s for s in db.list_speakers(mid)}
        self.assertEqual(by["Alice"]["attendance"], "present")            # พูดแล้ว = เข้าร่วม
        self.assertEqual((by["Bob"]["source"], by["Bob"]["attendance"]), ("meet", "present"))
        self.assertEqual(by["Dan A"]["attendance"], "invited")            # ไม่ถูกเดาว่าเป็นใคร
        self.assertEqual(db.get_or_create_speaker(mid, "Bob"), got["Bob"])

    def test_merge_speakers_moves_segments_and_remembers_alias(self):
        _, mid = self.new_meeting()
        real = db.add_speaker(mid, "ธนาวีร์ บุญเกิด", email="t@x.com", attendance="invited")
        discovered = db.get_or_create_speaker(mid, "46 ธนาวีร์ บุญเกิด")  # Meet แสดงชื่อพ่วงเลข
        self.assertNotEqual(real, discovered)
        db.insert_segment(mid, discovered, 1, "สวัสดี")
        db.insert_segment(mid, discovered, 2, "ขอบคุณ")
        self.assertEqual(db.merge_speakers(discovered, real), 2)
        people = db.list_speakers(mid)
        self.assertEqual([(s["display_name"], s["segment_count"], s["attendance"], s["meet_alias"]) for s in people],
                         [("ธนาวีร์ บุญเกิด", 2, "present", "46 ธนาวีร์ บุญเกิด")])
        self.assertEqual({t["display_name"] for t in db.get_transcript(mid)}, {"ธนาวีร์ บุญเกิด"})
        self.assertEqual(db.get_or_create_speaker(mid, "46 ธนาวีร์ บุญเกิด"), real)   # ครั้งหน้าจับคู่ให้เอง ไม่สร้างซ้ำ
        # ข้อห้าม
        other = db.get_or_create_speaker(mid, "คนอื่น")
        with self.assertRaises(ValueError):
            db.merge_speakers(real, real)
        _, mid2 = self.new_meeting()
        foreign = db.add_speaker(mid2, "ต่างประชุม")
        with self.assertRaisesRegex(ValueError, "การประชุมเดียวกัน"):
            db.merge_speakers(other, foreign)
        chair = db.add_speaker(mid, "ประธาน", role="chair")
        with self.assertRaisesRegex(ValueError, "ประธาน/เลขา"):
            db.merge_speakers(chair, real)
        with self.assertRaisesRegex(ValueError, "มีข้อความ"):
            db.delete_speaker(real)

    def test_is_meeting_manager_by_owner_or_chair_email(self):
        uid, mid = self.new_meeting()
        db.add_speaker(mid, "ประธาน", email="Chair@X.com", role="chair")
        db.add_speaker(mid, "เลขา", email="sec@x.com", role="secretary")
        db.add_speaker(mid, "ผู้เข้าร่วม", email="member@x.com")
        m = db.get_meeting(mid)
        self.assertTrue(db.is_meeting_manager(m, uid, None))
        self.assertTrue(db.is_meeting_manager(m, 999, "chair@x.com"))          # ไม่สนตัวพิมพ์
        self.assertTrue(db.is_meeting_manager(m, 999, "sec@x.com"))
        self.assertFalse(db.is_meeting_manager(m, 999, "member@x.com"))        # ผู้เข้าร่วมธรรมดาไม่ใช่ผู้จัดการ
        self.assertFalse(db.is_meeting_manager(m, 999, None))
        other_uid = db.upsert_user("sub-o", "o@x.com", "O", None)
        self.assertEqual([x["meeting_id"] for x in db.list_meetings(other_uid, "chair@x.com")], [mid])
        self.assertEqual(db.list_meetings(other_uid, "member@x.com"), [])

    # ── transcript ──

    def test_segment_edit_keeps_original_and_deleted_are_excluded(self):
        _, mid = self.started()
        s1 = db.get_or_create_speaker(mid, "Alice")
        a = db.insert_segment(mid, s1, 1, "ข้อความเดิม")
        b = db.insert_segment(mid, s1, 2, "ลบทิ้ง")
        db.edit_segment(a, text="ข้อความที่แก้")
        db.edit_segment(a, text="แก้อีกรอบ")
        seg = next(s for s in db.list_segments(mid) if s["segment_id"] == a)
        self.assertEqual((seg["text"], seg["original_text"]), ("แก้อีกรอบ", "ข้อความเดิม"))   # ต้นฉบับไม่ถูกทับ
        self.assertIsNotNone(seg["edited_at"])
        db.edit_segment(b, deleted=True)
        self.assertEqual([t["text"] for t in db.get_transcript(mid)], ["แก้อีกรอบ"])
        self.assertEqual(len(db.list_segments(mid)), 2)                       # ยังเห็นในหน้าตรวจ กู้คืนได้
        self.assertEqual(db.list_speakers(mid)[0]["segment_count"], 1)
        db.edit_segment(b, deleted=False)
        self.assertEqual(len(db.get_transcript(mid)), 2)
        with self.assertRaises(ValueError):
            db.edit_segment(a, text="   ")

    def test_changing_a_segments_speaker_creates_or_reuses_people_and_marks_attendance(self):
        _, mid = self.started()
        registered = db.add_speaker(mid, "Bob")
        s1 = db.get_or_create_speaker(mid, "ผู้พูดผิด")
        seg = db.insert_segment(mid, s1, 1, "ข้อความ")
        db.edit_segment(seg, speaker_name="Bob")
        self.assertEqual(next(s for s in db.list_segments(mid))["speaker_id"], registered)
        self.assertEqual(db.get_speaker(registered)["attendance"], "present")
        self.assertEqual(db.list_speakers(mid)[1]["segment_count"], 0)          # ผู้พูดผิดไม่มีข้อความเหลือ (ลบทิ้งได้)

    def test_max_sequence_no(self):
        _, mid = self.started()
        self.assertEqual(db.max_sequence_no(mid), 0)
        s = db.get_or_create_speaker(mid, "A")
        db.insert_segment(mid, s, 7, "x")
        self.assertEqual(db.max_sequence_no(mid), 7)

    # ── รายงาน ──

    def report_content(self, **over):
        c = {"summary": "สรุป", "other_matters": "เรื่องอื่น",
             "agenda": [{"title": "งบ", "discussion": "คุยงบ", "resolution": "อนุมัติ", "evidence": ["ข้อความอ้างอิง"]},
                        {"title": "ห้อง", "discussion": "ยังไม่สรุป", "resolution": None, "evidence": []}],
             "action_items": [{"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-09",
                               "due_time": "13:00", "due_time_end": None, "evidence": ["จะส่ง"]},
                              {"description": "จองห้อง", "assignee": None, "due_date": None,
                               "due_time": None, "due_time_end": None, "evidence": []}]}
        c.update(over)
        return c

    def to_draft(self):
        uid, mid = self.started()
        s = db.get_or_create_speaker(mid, "Alice")
        db.insert_segment(mid, s, 1, "x")
        db.end_meeting(mid)
        db.set_meeting_status(mid, "transcript_verified")
        db.set_meeting_status(mid, "draft")
        return uid, mid

    def test_report_roundtrip_stores_rows_not_json(self):
        _, mid = self.to_draft()
        saved = db.save_report(mid, self.report_content(), "gemini", "minutes_v2")
        rep = db.get_report(mid)
        self.assertEqual(rep["summary_id"], saved["summary_id"])
        self.assertEqual(rep["content"], {**self.report_content(), "agenda": [
            {**a, "section": "consider_new"} for a in self.report_content()["agenda"]], "action_items": [
            {**self.report_content()["action_items"][0], "action_item_id": rep["content"]["action_items"][0]["action_item_id"],
             "google_calendar_event_id": None, "google_calendar_link": None},
            {**self.report_content()["action_items"][1], "action_item_id": rep["content"]["action_items"][1]["action_item_id"],
             "google_calendar_event_id": None, "google_calendar_link": None}]})
        self.assertFalse(rep["approved"])
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM agenda_items")[0]["n"] >= 2, True)
        self.assertEqual(rep["ai_snapshot"]["summary"], "สรุป")

    def test_regenerating_replaces_the_working_draft_instead_of_stacking(self):
        _, mid = self.to_draft()
        first = db.save_report(mid, self.report_content(summary="รอบแรก"))
        again = db.save_report(mid, self.report_content(summary="รอบสอง", agenda=[]))
        self.assertEqual(again["summary_id"], first["summary_id"])                          # แถวเดิม ไม่มีร่างเก่าค้าง
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM summaries WHERE meeting_id=%s", (mid,))[0]["n"], 1)
        rep = db.get_report(mid)
        self.assertEqual((rep["content"]["summary"], rep["content"]["agenda"]), ("รอบสอง", []))
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM agenda_items WHERE summary_id=%s", (rep["summary_id"],))[0]["n"], 0)

    def test_approve_is_atomic_and_locks_the_report(self):
        uid, mid = self.to_draft()
        self.assertFalse(db.approve_report(mid, uid))                            # ยังไม่มีรายงาน
        db.save_report(mid, self.report_content())
        sid = db.get_report(mid)["summary_id"]
        db.set_meeting_status(mid, "transcript_review")                          # สถานะไม่ใช่ draft -> อนุมัติไม่ได้
        self.assertFalse(db.approve_report(mid, uid))
        self.assertFalse(db.get_report(mid)["approved"])                         # และรายงานต้องไม่ถูกแตะครึ่งๆ กลางๆ
        db.set_meeting_status(mid, "transcript_verified")
        db.set_meeting_status(mid, "draft")
        self.assertTrue(db.approve_report(mid, uid))
        rep = db.get_report(mid)
        self.assertTrue(rep["approved"])
        self.assertEqual(db.get_meeting(mid)["status"], "approved")
        self.assertFalse(db.update_report_content(sid, self.report_content(summary="แอบแก้")))   # ล็อก
        self.assertFalse(db.approve_report(mid, uid))
        self.assertEqual(db.get_report(mid)["content"]["summary"], "สรุป")

    def test_reopen_clears_approval_and_calendar_marks_but_keeps_content(self):
        uid, mid = self.to_draft()
        self.assertFalse(db.reopen_report(mid))                                  # ยังไม่อนุมัติ
        db.save_report(mid, self.report_content())
        db.approve_report(mid, uid)
        db.mark_action_item_synced(db.get_report(mid)["content"]["action_items"][0]["action_item_id"], "evt-1", "https://x/e")
        with self.assertRaises(ValueError):                                      # อนุมัติแล้วเขียนทับด้วยรายงานใหม่ไม่ได้
            db.save_report(mid, self.report_content(summary="แอบสร้างใหม่"))
        self.assertTrue(db.reopen_report(mid))
        self.assertEqual(db.get_meeting(mid)["status"], "draft")
        rep = db.get_report(mid)
        self.assertEqual((rep["approved"], rep["approved_by"], rep["approved_at"]), (False, None, None))
        self.assertEqual(rep["content"]["summary"], "สรุป")
        self.assertEqual(rep["content"]["agenda"][0]["evidence"], ["ข้อความอ้างอิง"])
        self.assertEqual([(i["google_calendar_event_id"], i["google_calendar_link"]) for i in rep["content"]["action_items"]],
                         [(None, None), (None, None)])                           # นัดถูกลบแล้ว ต้องส่งใหม่ตอนอนุมัติครั้งหน้า
        self.assertEqual(len(rep["content"]["action_items"]), 2)
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM summaries WHERE meeting_id=%s", (mid,))[0]["n"], 1)
        # แก้ได้อีกครั้งและต้องอนุมัติใหม่
        content = rep["content"]
        content["summary"] = "สรุปที่แก้หลังยกเลิกการอนุมัติ"
        self.assertTrue(db.update_report_content(rep["summary_id"], content))
        self.assertTrue(db.approve_report(mid, uid))
        self.assertEqual(db.get_report(mid)["content"]["summary"], "สรุปที่แก้หลังยกเลิกการอนุมัติ")

    def test_meeting_status_and_report_state_never_disagree(self):
        uid, mid = self.to_draft()
        db.save_report(mid, self.report_content())
        self.assertEqual((db.get_meeting(mid)["status"], db.get_report(mid)["approved"]), ("draft", False))
        db.approve_report(mid, uid)
        self.assertEqual((db.get_meeting(mid)["status"], db.get_report(mid)["approved"]), ("approved", True))
        db.reopen_report(mid)
        self.assertEqual((db.get_meeting(mid)["status"], db.get_report(mid)["approved"]), ("draft", False))

    # ── อื่นๆ ──

    def test_list_meetings_is_scoped_and_counts_segments(self):
        a, ma = self.started()
        b, mb = self.started()
        s = db.get_or_create_speaker(ma, "A")
        db.insert_segment(ma, s, 1, "x")
        listed = db.list_meetings(a)
        self.assertEqual([(m["meeting_id"], m["segment_count"]) for m in listed], [(ma, 1)])
        self.assertEqual([m["meeting_id"] for m in db.list_meetings(b)], [mb])

    def test_deleting_a_meeting_cascades_everything(self):
        uid, mid = self.to_draft()
        db.add_speaker(mid, "x")
        db.save_report(mid, self.report_content())
        self.execute("DELETE FROM meetings WHERE meeting_id = %s", (mid,))
        for table in ("speakers", "transcript_segments", "summaries"):
            self.assertEqual(self.rows(f"SELECT COUNT(*) n FROM {table} WHERE meeting_id=%s", (mid,))[0]["n"], 0, table)
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM agenda_items a LEFT JOIN summaries s ON s.summary_id=a.summary_id WHERE s.summary_id IS NULL")[0]["n"], 0)

    def test_calendar_marks_store_event_and_link(self):
        uid = db.upsert_user("sub-cal", "cal@x.com", "C", None)
        mid = db.create_meeting_setup(uid, meet_url=URL)
        db.save_report(mid, {"summary": "x", "agenda": [], "other_matters": None, "action_items": [
            {"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-16", "evidence": []},
            {"description": "จองห้อง", "evidence": []}]})
        ids = [i["action_item_id"] for i in db.get_report(mid)["content"]["action_items"]]
        db.mark_action_item_synced(ids[0], "evt-1", "https://calendar.google.com/e/1")
        first, second = db.get_report(mid)["content"]["action_items"]
        self.assertEqual((first["google_calendar_event_id"], first["google_calendar_link"]), ("evt-1", "https://calendar.google.com/e/1"))
        self.assertEqual((second["google_calendar_event_id"], second["google_calendar_link"]), (None, None))

    def test_calendar_token_lives_on_the_user(self):
        a = db.upsert_user("sub-t1", "t1@x.com", "T1", None)
        b = db.upsert_user("sub-t2", "t2@x.com", "T2", None)
        db.save_calendar_token(a, '{"a": 1}')
        self.assertEqual(db.get_calendar_token(a), '{"a": 1}')
        self.assertIsNone(db.get_calendar_token(b))

    def test_upsert_user_is_idempotent(self):
        first = db.upsert_user("sub-same", "s@x.com", "ชื่อเดิม", None)
        again = db.upsert_user("sub-same", "s2@x.com", "ชื่อใหม่", "http://pic")
        self.assertEqual(first, again)
        self.assertEqual(self.rows("SELECT name FROM users WHERE user_id=%s", (first,))[0]["name"], "ชื่อใหม่")


class NormalizeNameTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(db.normalize_name("  Alice   Smith (You) "), "alice smith")
        self.assertEqual(db.normalize_name("สมชาย (คุณ)"), "สมชาย")
        self.assertEqual(db.normalize_name("Bob (Young)"), "bob (young)")   # วงเล็บอื่นต้องไม่ถูกตัด
        self.assertEqual(db.normalize_name(""), "")


if __name__ == "__main__":
    unittest.main()
