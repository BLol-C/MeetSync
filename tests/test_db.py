"""
ทดสอบชั้นข้อมูล (db.py) กับ MySQL จริงในฐานข้อมูลชั่วคราว meetsync_test_<สุ่ม> — สร้างและลบเองทุกครั้ง
ไม่แตะฐานข้อมูลจริงของแอป (DB_NAME ใน .env) ถ้าต่อ MySQL ไม่ได้ ทุกเทสต์จะถูกข้าม (skip)

รัน:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import subprocess
import types
import unittest
import uuid

import db
from tests.dbcase import ROOT, TempDbCase

OLD_SCHEMA_COMMIT = "c582c90"   # db.py ก่อนมีระบบ owner / participants / migration แบบมีเวอร์ชัน
URL = "https://meet.google.com/abc-defg-hij"


class MigrationTests(TempDbCase):
    def test_upgrade_from_old_schema_keeps_data(self):
        try:
            src = subprocess.run(
                ["git", "show", f"{OLD_SCHEMA_COMMIT}:db.py"],
                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
            ).stdout
        except Exception as e:  # noqa: BLE001
            self.skipTest(f"อ่าน db.py เวอร์ชันเก่าจาก git ไม่ได้: {e!r}")

        old = types.ModuleType("old_db")
        exec(compile(src, "old_db.py", "exec"), old.__dict__)  # noqa: S102 — โค้ดของ repo เราเอง
        old.DB_NAME = self.dbname
        old.init_schema()
        running = old.create_meeting(URL)                       # สถานะเก่า: in_progress
        finished = old.create_meeting(URL)
        old.end_meeting(finished)                               # สถานะเก่า: completed
        sp = old.get_or_create_speaker(finished, "สมชาย")
        old.insert_segment(finished, sp, 1, "สวัสดีครับ")
        summary_id = old.insert_summary(finished, "สรุปเดิม", "gemini")
        old.insert_action_item(summary_id, "ส่งรายงาน", "สมชาย", "2026-10-30")

        db.init_schema()
        db.init_schema()  # รันซ้ำต้องไม่พังและไม่ทำอะไรซ้ำ

        status = {r["meeting_id"]: r["status"] for r in self.rows("SELECT meeting_id, status FROM meetings")}
        self.assertEqual(status[running], "recording")
        self.assertEqual(status[finished], "transcript_review")
        self.assertIsNone(self.rows("SELECT owner_user_id FROM meetings WHERE meeting_id=%s", (finished,))[0]["owner_user_id"])

        cols = {(r["TABLE_NAME"], r["COLUMN_NAME"]) for r in self.rows(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s", (self.dbname,))}
        for expected in [("meetings", "owner_user_id"), ("meetings", "venue"), ("meetings", "scheduled_at"),
                         ("meetings", "meeting_no"), ("meetings", "org_name"), ("speakers", "participant_id"),
                         ("transcript_segments", "original_text"), ("transcript_segments", "edited_at"),
                         ("transcript_segments", "deleted"), ("participants", "role"), ("minutes", "content")]:
            self.assertIn(expected, cols)

        versions = [r["version"] for r in self.rows("SELECT version FROM schema_migrations ORDER BY version")]
        self.assertEqual(versions, [v for v, _ in db._MIGRATIONS])

        # ข้อมูลเดิมยังอยู่ครบ และอ่านผ่านโค้ดใหม่ได้
        self.assertEqual(db.get_transcript(finished)[0]["text"], "สวัสดีครับ")
        self.assertEqual(db.get_summary(finished)["executive_summary"], "สรุปเดิม")
        # ฐานข้อมูลที่เพิ่งอัปเกรด: ประชุมใหม่ต้องได้ status เริ่มต้นเป็น recording
        uid = db.upsert_user("sub-mig", "m@x.com", "M", None)
        self.assertEqual(db.get_meeting(db.create_meeting(URL, uid))["status"], "recording")


class WorkflowTests(TempDbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.init_schema()

    def new_meeting(self):
        uid = db.upsert_user(f"sub-{uuid.uuid4().hex[:6]}", "a@b.com", "A", None)
        return uid, db.create_meeting(URL, uid)

    def test_end_meeting_moves_to_review_and_never_goes_back(self):
        _, mid = self.new_meeting()
        self.assertEqual(db.get_meeting(mid)["status"], "recording")
        db.end_meeting(mid)
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_review")
        self.assertIsNotNone(db.get_meeting(mid)["ended_at"])
        db.set_meeting_status(mid, "transcript_verified")
        db.end_meeting(mid)  # เรียกซ้ำ (เช่น stop ซ้อน) ต้องไม่ดึงสถานะถอยหลัง
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")

    def test_status_transitions(self):
        _, mid = self.new_meeting()
        self.assertFalse(db.set_meeting_status(mid, "draft"))               # ข้ามขั้นไม่ได้
        self.assertFalse(db.set_meeting_status(mid, "approved"))
        db.end_meeting(mid)
        self.assertFalse(db.set_meeting_status(mid, "draft"))               # ยังไม่ได้ verify
        self.assertTrue(db.set_meeting_status(mid, "transcript_verified"))
        self.assertFalse(db.set_meeting_status(mid, "approved"))
        self.assertTrue(db.set_meeting_status(mid, "draft"))
        self.assertTrue(db.set_meeting_status(mid, "draft"))                # สร้างรายงานใหม่ซ้ำได้
        self.assertTrue(db.set_meeting_status(mid, "transcript_review"))    # กลับไปแก้ transcript ได้
        self.assertTrue(db.set_meeting_status(mid, "transcript_verified"))
        self.assertTrue(db.set_meeting_status(mid, "draft"))
        self.assertTrue(db.set_meeting_status(mid, "approved"))
        for s in ("transcript_review", "transcript_verified", "draft", "approved"):
            self.assertFalse(db.set_meeting_status(mid, s), f"approved แล้วต้องล็อก ({s})")
        with self.assertRaises(ValueError):
            db.set_meeting_status(mid, "nonsense")

    def test_roles_chair_and_secretary_are_unique(self):
        _, mid = self.new_meeting()
        chair = db.add_participant(mid, "ประธาน ก", role="chair")
        db.add_participant(mid, "เลขา ข", role="secretary")
        with self.assertRaisesRegex(ValueError, "มีประธานแล้ว"):
            db.add_participant(mid, "คนอื่น", role="chair")
        member = db.add_participant(mid, "สมาชิก ค")
        with self.assertRaisesRegex(ValueError, "มีเลขาแล้ว"):
            db.update_participant(member, role="secretary")
        db.update_participant(chair, role="attendee")                    # ปลดประธานเดิมแล้วตั้งคนใหม่ได้
        db.update_participant(member, role="chair")
        self.assertEqual(db.get_participant(member)["role"], "chair")
        db.update_participant(member, role="chair", display_name="สมาชิก ค (ประธาน)")  # ตัวเองถือบทบาทอยู่แล้วต้องไม่ชนตัวเอง

    def test_participant_validation(self):
        _, mid = self.new_meeting()
        with self.assertRaises(ValueError):
            db.add_participant(mid, "   ")
        with self.assertRaises(ValueError):
            db.add_participant(mid, "x", role="boss")
        with self.assertRaises(ValueError):
            db.add_participant(mid, "x", attendance="maybe")
        pid = db.add_participant(mid, " ทดสอบ ", email="  ")
        p = db.get_participant(pid)
        self.assertEqual((p["display_name"], p["email"], p["role"], p["attendance"]), ("ทดสอบ", None, "attendee", "invited"))
        with self.assertRaises(ValueError):
            db.update_participant(pid, meeting_id=999)                  # ฟิลด์ที่ไม่อนุญาตให้แก้
        db.update_participant(pid, attendance="absent", email="t@x.com")
        self.assertEqual(db.get_participant(pid)["attendance"], "absent")
        db.delete_participant(pid)
        self.assertIsNone(db.get_participant(pid))

    def test_speaker_auto_link_by_name(self):
        _, mid = self.new_meeting()
        somchai = db.add_participant(mid, "สมชาย  ใจดี")
        alice = db.add_participant(mid, "Alice")
        db.add_participant(mid, "Dan")
        db.add_participant(mid, "dan")                                   # ชื่อซ้ำกัน -> ห้ามเดา
        by_name = lambda n: next(s for s in db.list_speakers(mid) if s["display_name"] == n)  # noqa: E731

        db.get_or_create_speaker(mid, "Alice (You)")                    # Meet เติม (You) ให้ชื่อตัวเอง
        db.get_or_create_speaker(mid, "สมชาย ใจดี (คุณ)")
        db.get_or_create_speaker(mid, "Dan")
        db.get_or_create_speaker(mid, "Bob")
        self.assertEqual(by_name("Alice (You)")["participant_id"], alice)
        self.assertEqual(by_name("สมชาย ใจดี (คุณ)")["participant_id"], somchai)
        self.assertIsNone(by_name("Dan")["participant_id"])
        self.assertIsNone(by_name("Bob")["participant_id"])
        self.assertEqual(db.get_participant(alice)["attendance"], "present")   # มีเสียงพูด = เข้าร่วม
        # เรียกซ้ำได้ผู้พูดคนเดิม
        self.assertEqual(db.get_or_create_speaker(mid, "Bob"), by_name("Bob")["speaker_id"])

    def test_manual_link_unlink_and_cross_meeting_guard(self):
        _, mid = self.new_meeting()
        _, other = self.new_meeting()
        pid = db.add_participant(mid, "บ๊อบ")
        foreign = db.add_participant(other, "คนประชุมอื่น")
        sid = db.get_or_create_speaker(mid, "Bob")
        db.link_speaker(sid, pid)
        self.assertEqual(db.get_participant(pid)["attendance"], "present")
        with self.assertRaises(ValueError):
            db.link_speaker(sid, foreign)
        db.update_participant(pid, attendance="absent")
        db.link_speaker(sid, None)                                       # ยกเลิกการผูก
        self.assertIsNone(next(s for s in db.list_speakers(mid))["participant_id"])
        self.assertEqual(db.get_participant(pid)["attendance"], "absent")   # คนที่เลขาตั้งว่าไม่มา ไม่ถูกเปลี่ยนเอง

    def test_auto_link_after_participants_added_late(self):
        _, mid = self.new_meeting()
        db.get_or_create_speaker(mid, "Carol")
        self.assertEqual(db.auto_link_speakers(mid), 0)
        pid = db.add_participant(mid, "carol")
        self.assertEqual(db.auto_link_speakers(mid), 1)
        self.assertEqual(db.list_speakers(mid)[0]["participant_id"], pid)

    def test_deleted_segments_are_excluded(self):
        _, mid = self.new_meeting()
        sid = db.get_or_create_speaker(mid, "Alice")
        keep = db.insert_segment(mid, sid, 1, "เก็บไว้")
        gone = db.insert_segment(mid, sid, 2, "ลบทิ้ง")
        self.execute("UPDATE transcript_segments SET deleted = TRUE WHERE segment_id = %s", (gone,))
        self.assertEqual([r["text"] for r in db.get_transcript(mid)], ["เก็บไว้"])
        self.assertEqual(db.list_speakers(mid)[0]["segment_count"], 1)
        self.assertIsNotNone(keep)

    def test_minutes_versions_keep_history(self):
        _, mid = self.new_meeting()
        self.assertIsNone(db.get_latest_minutes(mid))
        v1 = db.insert_minutes(mid, {"agenda": [{"title": "เรื่องที่ 1"}]}, "gemini", "p1")
        v2 = db.insert_minutes(mid, {"agenda": [{"title": "เรื่องที่ 1 (แก้)"}]}, "gemini", "p2")
        self.assertEqual((v1["version"], v2["version"]), (1, 2))
        latest = db.get_latest_minutes(mid)
        self.assertEqual(latest["version"], 2)
        self.assertEqual(latest["content"]["agenda"][0]["title"], "เรื่องที่ 1 (แก้)")   # ภาษาไทยไม่เพี้ยน
        self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM minutes WHERE meeting_id=%s", (mid,))[0]["n"], 2)

    def test_list_meetings_is_scoped_to_owner(self):
        a, ma = self.new_meeting()
        b, mb = self.new_meeting()
        self.assertEqual([m["meeting_id"] for m in db.list_meetings(a)], [ma])
        self.assertEqual([m["meeting_id"] for m in db.list_meetings(b)], [mb])

    def test_deleting_meeting_cascades_to_participants_and_minutes(self):
        _, mid = self.new_meeting()
        db.add_participant(mid, "x")
        db.insert_minutes(mid, {})
        self.execute("DELETE FROM meetings WHERE meeting_id = %s", (mid,))
        self.assertEqual(db.list_participants(mid), [])
        self.assertIsNone(db.get_latest_minutes(mid))


class NormalizeNameTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(db.normalize_name("  Alice   Smith (You) "), "alice smith")
        self.assertEqual(db.normalize_name("สมชาย (คุณ)"), "สมชาย")
        self.assertEqual(db.normalize_name("Bob (Young)"), "bob (young)")   # วงเล็บอื่นต้องไม่ถูกตัด
        self.assertEqual(db.normalize_name(""), "")


if __name__ == "__main__":
    unittest.main()
