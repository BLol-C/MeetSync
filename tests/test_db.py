"""
ทดสอบชั้นข้อมูล (db.py) กับ MySQL จริงในฐานข้อมูลชั่วคราว meetsync_test_<สุ่ม> — สร้างและลบเองทุกครั้ง
ไม่แตะฐานข้อมูลจริงของแอป (DB_NAME ใน .env) ถ้าต่อ MySQL ไม่ได้ ทุกเทสต์จะถูกข้าม (skip)

รัน:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import subprocess
import types
import unittest
import uuid

from tests.dbcase import ROOT, TempDbCase, server_conn   # ต้อง import ก่อน db: โหลด .env ให้เรียบร้อยก่อน

import db  # noqa: E402

SA_ERA_COMMIT = "c582c90"    # db.py ตาม SA เดิม 6 ตาราง (ก่อนมี owner / participants / migration แบบมีเวอร์ชัน)
DEV_COMMIT = "7035ad5"       # db.py ช่วงพัฒนา (มี participants / minutes / calendar_tokens แยกตาราง) ก่อนยุบให้ตรง SA
URL = "https://meet.google.com/abc-defg-hij"


def load_old_db(commit: str, dbname: str):
    """โหลด db.py ของ commit เก่ามาเป็นโมดูล ชี้ไปที่ฐานข้อมูลที่ระบุ — ไว้สร้างฐานข้อมูล "เวอร์ชันเก่า" ที่มีข้อมูลจริงในตาราง"""
    try:
        src = subprocess.run(
            ["git", "show", f"{commit}:db.py"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except Exception as e:  # noqa: BLE001
        raise unittest.SkipTest(f"อ่าน db.py ของ {commit} จาก git ไม่ได้: {e!r}")
    old = types.ModuleType(f"old_db_{commit}")
    exec(compile(src, f"old_db_{commit}.py", "exec"), old.__dict__)  # noqa: S102 — โค้ดของ repo เราเอง
    old.DB_NAME = dbname
    return old


def make_db(name: str):
    conn = server_conn()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    conn.close()


def drop_db(name: str):
    conn = server_conn()
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    conn.close()


def schema_signature(dbname: str) -> dict:
    """ลายเซ็นโครงสร้างฐานข้อมูล: คอลัมน์ (ชนิด/null/ค่าเริ่มต้น), foreign key, unique key — ไว้เทียบว่าสองฐานเหมือนกันไหม"""
    conn = server_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
                   FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s""",
                (dbname,),
            )
            cols = {(r[0], r[1], r[2].lower(), r[3], None if r[4] is None else str(r[4]).strip("'")) for r in cur.fetchall()}
            cur.execute(
                """SELECT k.TABLE_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, r.DELETE_RULE
                   FROM information_schema.KEY_COLUMN_USAGE k
                   JOIN information_schema.REFERENTIAL_CONSTRAINTS r
                     ON r.CONSTRAINT_SCHEMA = k.TABLE_SCHEMA AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME
                    AND r.TABLE_NAME = k.TABLE_NAME
                   WHERE k.TABLE_SCHEMA = %s""",
                (dbname,),
            )
            fks = {tuple(r) for r in cur.fetchall()}
            cur.execute(
                """SELECT TABLE_NAME, INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX)
                   FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s AND NON_UNIQUE = 0
                   GROUP BY TABLE_NAME, INDEX_NAME""",
                (dbname,),
            )
            uniques = {(r[0], r[2]) for r in cur.fetchall()}   # ไม่เทียบชื่อ index เพราะ MySQL ตั้งชื่ออัตโนมัติต่างกันได้
        return {"columns": cols, "fks": fks, "uniques": uniques}
    finally:
        conn.close()


class MigrationTests(TempDbCase):
    """อัปเกรดจากฐานข้อมูลเดิมทุกยุคแล้วข้อมูลต้องครบ และโครงสร้างต้องเหมือนฐานข้อมูลที่สร้างใหม่"""

    per_test = True   # แต่ละเคสสร้างฐานข้อมูลเวอร์ชันเก่าของตัวเอง ต้องเริ่มจากฐานเปล่า

    def test_upgrade_from_sa_era_keeps_data_and_matches_fresh_schema(self):
        old = load_old_db(SA_ERA_COMMIT, self.dbname)
        old.init_schema()
        running = old.create_meeting(URL)                       # สถานะเก่า: in_progress
        finished = old.create_meeting(URL)
        old.end_meeting(finished)                               # สถานะเก่า: completed
        sp = old.get_or_create_speaker(finished, "สมชาย")
        old.insert_segment(finished, sp, 1, "สวัสดีครับ")
        summary_id = old.insert_summary(finished, "สรุปเดิม", "gemini")
        old.insert_action_item(summary_id, "ส่งรายงาน", "สมชาย", "2026-10-30")

        db.init_schema()
        db.init_schema()   # รันซ้ำต้องไม่พังและไม่ทำอะไรซ้ำ

        status = {r["meeting_id"]: r["status"] for r in self.rows("SELECT meeting_id, status FROM meetings")}
        self.assertEqual(status[running], "recording")
        self.assertEqual(status[finished], "transcript_review")
        self.assertEqual([r["version"] for r in self.rows("SELECT version FROM schema_migrations ORDER BY version")],
                         [v for v, _ in db._MIGRATIONS])
        # ผู้พูดเดิมมาจากชื่อใน Meet และเคยพูดจริง -> เข้าร่วม
        sp_row = db.list_speakers(finished)[0]
        self.assertEqual((sp_row["display_name"], sp_row["source"], sp_row["attendance"], sp_row["segment_count"]),
                         ("สมชาย", "meet", "present", 1))
        self.assertEqual(db.get_transcript(finished)[0]["text"], "สวัสดีครับ")
        # สรุปเดิมของ SA = รายงานฉบับร่าง (ยังไม่เคยผ่านการอนุมัติ) และงานเดิมยังผูกอยู่
        report = db.get_report(finished)
        self.assertFalse(report["approved"])
        self.assertEqual(report["content"]["summary"], "สรุปเดิม")
        self.assertEqual([(a["description"], a["assignee"], a["due_date"]) for a in report["content"]["action_items"]],
                         [("ส่งรายงาน", "สมชาย", "2026-10-30")])

        fresh = f"meetsync_fresh_{uuid.uuid4().hex[:6]}"
        make_db(fresh)
        orig, db.DB_NAME = db.DB_NAME, fresh
        try:
            db.init_schema()
        finally:
            db.DB_NAME = orig
        try:
            self.assertEqual(schema_signature(fresh), schema_signature(self.dbname),
                             "ฐานข้อมูลที่อัปเกรดต้องมีโครงสร้างเหมือนฐานข้อมูลที่สร้างใหม่ทุกคอลัมน์")
        finally:
            drop_db(fresh)

    def test_upgrade_from_development_schema_merges_duplicates(self):
        """ช่วงพัฒนามี participants/minutes/calendar_tokens แยกตาราง ทำให้คนเดียวอยู่สองที่และรายงานซ้อนกัน"""
        old = load_old_db(DEV_COMMIT, self.dbname)
        old.init_schema()
        uid = old.upsert_user("sub-dev", "dev@x.com", "เจ้าของ", None)
        old.save_calendar_token(uid, '{"token": "abc"}')

        # ประชุม A: รายชื่อ + คนเดียวกันโผล่เป็นสองผู้พูด + รายงานร่างเก่าค้าง + รายงานที่อนุมัติแล้ว
        a = old.create_meeting_setup(uid, meet_url=URL, title="ประชุม A")
        old.add_participant(a, "สมชาย ใจดี", "chair@x.com", "chair", "present")
        old.add_participant(a, "Alice", "alice@x.com")
        old.add_participant(a, "Bob", None, "attendee", "absent")
        old.begin_recording(a)
        s_chair = old.get_or_create_speaker(a, "สมชาย ใจดี (You)")      # จับคู่ chair อัตโนมัติ
        s_alice1 = old.get_or_create_speaker(a, "Alice")
        s_alice2 = old.get_or_create_speaker(a, "Alice (You)")           # คนเดียวกัน Meet เติม (You) -> ผู้พูดซ้ำ (เหมือนกรณีจริง)
        s_zed = old.get_or_create_speaker(a, "Zed")                      # ไม่มีในรายชื่อ
        for i, sid in enumerate([s_chair, s_alice1, s_alice2, s_zed], start=1):
            old.insert_segment(a, sid, i, f"ข้อความ {i}")
        old.end_meeting(a)
        old.set_meeting_status(a, "transcript_verified")
        old.set_meeting_status(a, "draft")
        content = {"summary": "สรุป A", "other_matters": "ไม่มี",
                   "agenda": [{"title": "งบ", "discussion": "คุยเรื่องงบ", "resolution": "อนุมัติ", "evidence": ["ผมเสนอ"]}],
                   "action_items": [{"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-09",
                                     "due_time": "13:00", "due_time_end": None, "evidence": ["ส่งรายงาน"]},
                                    {"description": "จองห้อง", "assignee": None, "due_date": None,
                                     "due_time": None, "due_time_end": None, "evidence": []}]}
        old.insert_minutes(a, {**content, "summary": "ร่างเก่าที่ถูกแทนที่"}, "gemini", "minutes_v1")   # v1 ค้างเป็นร่าง
        old.insert_minutes(a, content, "gemini", "minutes_v2")                                          # v2
        self.assertTrue(old.approve_minutes(a, uid, content["action_items"]))                           # v2 อนุมัติ + มีแถวงาน
        item_id = old.list_minutes_action_items(old.get_latest_minutes(a)["minutes_id"])[0]["action_item_id"]
        old.mark_action_item_synced(item_id, "evt-1")                                                   # ส่ง Calendar ไปแล้วหนึ่งงาน

        # ประชุม B: มีแต่ร่างเดียว
        b = old.create_meeting_setup(uid, meet_url=URL, title="ประชุม B")
        old.begin_recording(b)
        sb = old.get_or_create_speaker(b, "ใครสักคน")
        old.insert_segment(b, sb, 1, "ข้อความ B")
        old.end_meeting(b)
        old.set_meeting_status(b, "transcript_verified")
        old.set_meeting_status(b, "draft")
        old.insert_minutes(b, {"summary": "ร่าง B", "other_matters": None, "agenda": [],
                               "action_items": [{"description": "งาน B", "assignee": None, "due_date": None,
                                                 "due_time": None, "due_time_end": None, "evidence": []}]}, "gemini", "minutes_v2")

        # ประชุม C: มีสรุปแบบ SA เดิม และรายงานร่างใหม่ซ้อนกัน
        c = old.create_meeting(URL, uid)
        sc = old.get_or_create_speaker(c, "คนหนึ่ง")
        old.insert_segment(c, sc, 1, "ข้อความ C")
        sid = old.insert_summary(c, "สรุปเดิมของ C", "gemini")
        old.insert_action_item(sid, "งานเดิมของ C", None, None)
        old.insert_minutes(c, {"summary": "ร่างใหม่ของ C", "other_matters": None, "agenda": [], "action_items": []}, "gemini", "minutes_v2")

        # ประชุม D: อนุมัติแล้ว ส่ง Calendar ไปหนึ่งงาน แล้วกด "สร้างเวอร์ชันแก้ไข" (ร่างใหม่ซ้อนบนฉบับที่อนุมัติ)
        d = old.create_meeting_setup(uid, meet_url=URL, title="ประชุม D")
        old.begin_recording(d)
        sd = old.get_or_create_speaker(d, "คน D")
        old.insert_segment(d, sd, 1, "ข้อความ D")
        old.end_meeting(d)
        old.set_meeting_status(d, "transcript_verified")
        old.set_meeting_status(d, "draft")
        d_items = [{"description": "งาน D", "assignee": None, "due_date": "2026-11-01", "due_time": None,
                    "due_time_end": None, "evidence": []}]
        old.insert_minutes(d, {"summary": "ฉบับอนุมัติของ D", "other_matters": None, "agenda": [], "action_items": d_items},
                           "gemini", "minutes_v2")
        old.approve_minutes(d, uid, d_items)
        old.mark_action_item_synced(old.list_minutes_action_items(old.get_latest_minutes(d)["minutes_id"])[0]["action_item_id"], "evt-d")
        old.revise_minutes(d)

        db.init_schema()
        db.init_schema()   # idempotent

        tables = {r["TABLE_NAME"] for r in self.rows(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s", (self.dbname,))}
        self.assertEqual(tables, {"users", "meetings", "speakers", "transcript_segments", "summaries",
                                  "agenda_items", "action_items", "schema_migrations"})
        self.assertEqual(db.get_calendar_token(uid), '{"token": "abc"}')

        # A: คนเดียวไม่ซ้ำ — Alice สองผู้พูดรวมเป็นแถวเดียว, ชื่อที่ Meet แสดงเก็บเป็น alias
        people = {s["display_name"]: s for s in db.list_speakers(a)}
        self.assertEqual(set(people), {"สมชาย ใจดี", "Alice", "Bob", "Zed"})
        self.assertEqual((people["สมชาย ใจดี"]["role"], people["สมชาย ใจดี"]["meet_alias"], people["สมชาย ใจดี"]["segment_count"]),
                         ("chair", "สมชาย ใจดี (You)", 1))
        self.assertEqual((people["Alice"]["segment_count"], people["Alice"]["meet_alias"], people["Alice"]["email"]),
                         (2, "Alice (You)", "alice@x.com"))
        self.assertEqual((people["Bob"]["attendance"], people["Bob"]["source"], people["Bob"]["segment_count"]),
                         ("absent", "registered", 0))
        self.assertEqual((people["Zed"]["source"], people["Zed"]["attendance"]), ("meet", "present"))
        self.assertEqual(len(db.get_transcript(a)), 4)

        # A: รายงานหนึ่งฉบับต่อประชุมตาม SA (ร่างเก่าที่ถูกแทนที่ไม่ตามมา) งานและสถานะส่ง Calendar ย้ายตามมา
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM summaries WHERE meeting_id=%s", (a,))[0]["n"], 1)
        rep = db.get_report(a)
        self.assertTrue(rep["approved"])
        self.assertEqual(rep["approved_by_name"], "เจ้าของ")
        self.assertEqual(rep["content"]["summary"], "สรุป A")
        self.assertEqual(rep["content"]["agenda"][0]["evidence"], ["ผมเสนอ"])
        self.assertEqual(rep["prompt_version"], "minutes_v2")
        self.assertEqual([(i["description"], i["calendar_synced"]) for i in rep["content"]["action_items"]],
                         [("ส่งรายงาน", True), ("จองห้อง", False)])

        # B: ร่างเดียวย้ายมาพร้อมงาน
        rep_b = db.get_report(b)
        self.assertEqual((rep_b["approved"], rep_b["content"]["summary"]), (False, "ร่าง B"))
        self.assertEqual([i["description"] for i in rep_b["content"]["action_items"]], ["งาน B"])

        # C: มีสรุปแบบ SA เดิมและรายงานใหม่ -> รายงานใหม่แทนที่ (เหมือนสร้างสรุปใหม่) เหลือแถวเดียว ไม่มีงานเดิมค้าง
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM summaries WHERE meeting_id=%s", (c,))[0]["n"], 1)
        rep_c = db.get_report(c)
        self.assertEqual((rep_c["content"]["summary"], rep_c["content"]["action_items"]), ("ร่างใหม่ของ C", []))

        # D: อนุมัติแล้วแก้ต่อเป็นร่าง -> เป็นฉบับร่างฉบับเดียว และงานที่ส่ง Calendar ไปแล้วยังจำได้ (ไม่ส่งซ้ำ)
        rep_d = db.get_report(d)
        self.assertFalse(rep_d["approved"])
        self.assertEqual([(i["description"], i["calendar_synced"], i["google_calendar_event_id"])
                          for i in rep_d["content"]["action_items"]], [("งาน D", True, "evt-d")])

        self.assertEqual(self.rows("SELECT COUNT(*) n FROM action_items WHERE summary_id IS NULL")[0]["n"], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) n FROM action_items")[0]["n"], 2 + 1 + 0 + 1)   # A สอง, B หนึ่ง, C ไม่มี, D หนึ่ง
        self.assertEqual({r["meeting_id"]: r["status"] for r in self.rows("SELECT meeting_id, status FROM meetings")},
                         {a: "approved", b: "draft", c: "recording", d: "draft"})


    def test_two_processes_starting_at_once_do_not_migrate_twice(self):
        """หน้าเว็บกับบริการบอทเรียก init_schema() ตอนเริ่มพร้อมกัน — ต้องไม่ย้ายข้อมูลซ้ำสองรอบจนข้อมูลซ้ำ"""
        import threading

        old = load_old_db(DEV_COMMIT, self.dbname)
        old.init_schema()
        uid = old.upsert_user("sub-race", "race@x.com", "R", None)
        m = old.create_meeting_setup(uid, meet_url=URL)
        old.add_participant(m, "Alice", "alice@x.com", "chair", "present")
        old.begin_recording(m)
        sp = old.get_or_create_speaker(m, "Alice (You)")
        old.insert_segment(m, sp, 1, "x")
        old.end_meeting(m)
        old.set_meeting_status(m, "transcript_verified")
        old.set_meeting_status(m, "draft")
        content = {"summary": "สรุป", "other_matters": None, "agenda": [{"title": "ก", "discussion": "", "resolution": None, "evidence": []}],
                   "action_items": [{"description": "งาน", "assignee": None, "due_date": None, "due_time": None,
                                     "due_time_end": None, "evidence": []}]}
        old.insert_minutes(m, content, "gemini", "minutes_v2")
        old.approve_minutes(m, uid, content["action_items"])

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
        counts = {t: self.rows(f"SELECT COUNT(*) n FROM {t}")[0]["n"] for t in ("speakers", "summaries", "agenda_items", "action_items")}
        self.assertEqual(counts, {"speakers": 1, "summaries": 1, "agenda_items": 1, "action_items": 1})
        self.assertEqual(db.list_speakers(m)[0]["meet_alias"], "Alice (You)")


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

    def test_fresh_database_records_all_migrations(self):
        self.assertEqual([r["version"] for r in self.rows("SELECT version FROM schema_migrations ORDER BY version")],
                         [v for v, _ in db._MIGRATIONS])

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
        self.assertEqual(rep["content"], {**self.report_content(), "action_items": [
            {**self.report_content()["action_items"][0], "action_item_id": rep["content"]["action_items"][0]["action_item_id"],
             "calendar_synced": False, "google_calendar_event_id": None},
            {**self.report_content()["action_items"][1], "action_item_id": rep["content"]["action_items"][1]["action_item_id"],
             "calendar_synced": False, "google_calendar_event_id": None}]})
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

    def test_edit_draft_keeps_calendar_sync_for_unchanged_items_only(self):
        _, mid = self.to_draft()
        db.save_report(mid, self.report_content())
        item = db.get_report(mid)["content"]["action_items"][0]
        db.mark_action_item_synced(item["action_item_id"], "evt-1")
        content = db.get_report(mid)["content"]
        content["action_items"][1]["description"] = "จองห้องใหญ่"              # แก้งานที่สอง
        self.assertTrue(db.update_report_content(db.get_report(mid)["summary_id"], content))
        items = db.get_report(mid)["content"]["action_items"]
        self.assertEqual([(i["description"], i["calendar_synced"], i["google_calendar_event_id"]) for i in items],
                         [("ส่งรายงาน", True, "evt-1"), ("จองห้องใหญ่", False, None)])   # งานที่ไม่ถูกแก้ไม่ถูกส่งซ้ำ
        content["action_items"][0]["due_date"] = "2026-10-10"                   # แก้วันที่ -> เป็นงานใหม่ใน Calendar
        db.update_report_content(db.get_report(mid)["summary_id"], content)
        self.assertFalse(db.get_report(mid)["content"]["action_items"][0]["calendar_synced"])

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

    def test_reopen_clears_approval_but_keeps_content_and_calendar_flags(self):
        uid, mid = self.to_draft()
        self.assertFalse(db.reopen_report(mid))                                  # ยังไม่อนุมัติ
        db.save_report(mid, self.report_content())
        db.mark_action_item_synced(db.get_report(mid)["content"]["action_items"][0]["action_item_id"], "evt-1")
        db.approve_report(mid, uid)
        with self.assertRaises(ValueError):                                      # อนุมัติแล้วเขียนทับด้วยรายงานใหม่ไม่ได้
            db.save_report(mid, self.report_content(summary="แอบสร้างใหม่"))
        self.assertTrue(db.reopen_report(mid))
        self.assertEqual(db.get_meeting(mid)["status"], "draft")
        rep = db.get_report(mid)
        self.assertEqual((rep["approved"], rep["approved_by"], rep["approved_at"]), (False, None, None))
        self.assertEqual(rep["content"]["summary"], "สรุป")
        self.assertEqual(rep["content"]["agenda"][0]["evidence"], ["ข้อความอ้างอิง"])
        self.assertTrue(rep["content"]["action_items"][0]["calendar_synced"])    # ไม่ส่งซ้ำ
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
        self.assertEqual(db.get_user_name(first), "ชื่อใหม่")


class NormalizeNameTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(db.normalize_name("  Alice   Smith (You) "), "alice smith")
        self.assertEqual(db.normalize_name("สมชาย (คุณ)"), "สมชาย")
        self.assertEqual(db.normalize_name("Bob (Young)"), "bob (young)")   # วงเล็บอื่นต้องไม่ถูกตัด
        self.assertEqual(db.normalize_name(""), "")


if __name__ == "__main__":
    unittest.main()
