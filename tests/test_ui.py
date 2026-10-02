"""
ทดสอบหน้าเว็บ Streamlit ด้วย AppTest (รันสคริปต์จริงโดยไม่ต้องมีเบราว์เซอร์) กับ MySQL ชั่วคราว
ใช้ผู้ใช้ทดสอบ (MEETSYNC_DEV_USER) และบริการบอท/AI ปลอม — พิสูจน์ว่าทุกสถานะของการประชุมเรนเดอร์ได้ไม่พัง
และปุ่มสำคัญเรียก service/บอทถูกที่ (การคลิกในตารางแก้ไขต้องดูด้วยเบราว์เซอร์: tools/ui_smoke.py)
"""

import os
import unittest
from unittest import mock

from tests.dbcase import ROOT, TempDbCase

os.environ["MEETSYNC_DEV_USER"] = "owner@x.com|เจ้าของ"
os.environ.setdefault("BOT_API_TOKEN", "test-token")

from streamlit.testing.v1 import AppTest  # noqa: E402

import botclient  # noqa: E402
import db  # noqa: E402
import service  # noqa: E402
import summarizer  # noqa: E402
from tests.test_service import fake_ai  # noqa: E402

URL = "https://meet.google.com/abc-defg-hij"
IDLE = {"running": False, "mine": False, "meeting_id": None, "rows": [], "status": [], "error": None}


def texts(elements):
    return [getattr(e, "value", None) or getattr(e, "label", "") for e in elements]


def button(at, label_part):
    return next(b for b in at.button if label_part in b.label)


class UiTests(TempDbCase):
    per_test = True   # แต่ละเทสต์เริ่มจากฐานเปล่า (รายการประชุมของเทสต์อื่นต้องไม่โผล่มาปน)

    def setUp(self):
        super().setUp()
        db.init_schema()
        self.user = {"user_id": db.upsert_user("dev:owner@x.com", "owner@x.com", "เจ้าของ", None),
                     "email": "owner@x.com", "name": "เจ้าของ"}
        self.bot = {"health": {"ok": True, "db": True, "running": False}, "state": dict(IDLE)}
        self.calls = []
        patches = [
            mock.patch.object(botclient, "health", lambda: self.bot["health"]),
            mock.patch.object(botclient, "state", lambda user: self.bot["state"]),
            mock.patch.object(botclient, "start", lambda user, mid: self.calls.append(("start", mid))),
            mock.patch.object(botclient, "stop", lambda user: self.calls.append(("stop",))),
            mock.patch.object(summarizer, "_gemini_generate", lambda: fake_ai),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def app(self, **query):
        at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=60)
        for k, v in query.items():
            at.query_params[k] = str(v)
        at.run()
        self.assertEqual([e.value for e in at.exception], [], "หน้าเว็บต้องไม่มี exception")
        return at

    def meeting(self, status="scheduled", people=True):
        mid = service.create_meeting(self.user, meet_url=URL, title="ประชุมทดสอบหน้าเว็บ", people=[
            {"display_name": "สมชาย ใจดี", "email": "chair@x.com", "role": "chair", "attendance": "present"},
            {"display_name": "สมหญิง", "role": "secretary", "attendance": "present"},
            {"display_name": "Alice", "email": "alice@x.com"}] if people else [])
        if status != "scheduled":
            db.begin_recording(mid)
            s1, s2 = db.get_or_create_speaker(mid, "สมชาย ใจดี (You)"), db.get_or_create_speaker(mid, "Alice")
            db.insert_segment(mid, s1, 1, "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท")
            db.insert_segment(mid, s2, 2, "เห็นด้วยค่ะ ดิฉันจะส่งรายงานความก้าวหน้าวันศุกร์หน้า")
            if status != "recording":
                db.end_meeting(mid)
        if status in ("transcript_verified", "draft", "approved"):
            service.verify_transcript(self.user, mid)
        if status in ("draft", "approved"):
            service.generate_report(self.user, mid, generate=fake_ai)
        if status == "approved":
            service.approve_report(self.user, mid, confirm_warnings=True)
        return mid

    # ── หน้ารวมและหน้าสร้าง ──

    def test_home_empty_then_lists_meetings_with_status_and_next_step(self):
        at = self.app()
        self.assertTrue(any("ยังไม่มีการประชุม" in v for v in texts(at.info)))
        mid = self.meeting("transcript_review")
        at = self.app()
        self.assertEqual(at.title[0].value, "การประชุมของฉัน")
        body = " ".join(texts(at.markdown) + texts(at.caption))
        self.assertIn("ประชุมทดสอบหน้าเว็บ", body)
        self.assertIn("รอตรวจ transcript", body)
        self.assertIn("ตรวจทานข้อความที่บอทจับได้", body)               # บอกขั้นตอนต่อไปในรายการเลย
        button(at, "เปิด").click().run()
        self.assertIn("ประชุมทดสอบหน้าเว็บ", at.title[0].value)        # คลิกแล้วเข้าหน้าการประชุมนั้น

    def test_filter_shows_only_unapproved_or_approved(self):
        done = self.meeting("approved")
        pending = self.meeting("scheduled")
        at = self.app()
        at.radio[0].set_value("อนุมัติแล้ว").run()
        keys = [b.key for b in at.button if (b.key or "").startswith("open_")]
        self.assertEqual(keys, [f"open_{done}"])
        at.radio[0].set_value("ต้องดำเนินการ").run()
        keys = [b.key for b in at.button if (b.key or "").startswith("open_")]
        self.assertIn(f"open_{pending}", keys)
        self.assertNotIn(f"open_{done}", keys)

    def test_new_meeting_form_creates_a_scheduled_meeting_and_opens_it(self):
        at = self.app(view="new")
        self.assertEqual(at.title[0].value, "สร้างการประชุมใหม่")
        at.text_input[0].set_value("https://example.com/not-meet")
        submit = next(b for b in at.button if b.label == "สร้างการประชุม")
        submit.click().run()
        self.assertTrue(any("ลิงก์ไม่ถูกต้อง" in v for v in texts(at.error)))
        at.text_input[0].set_value(URL)
        at.text_input[1].set_value("ประชุมที่สร้างจากฟอร์ม")
        next(b for b in at.button if b.label == "สร้างการประชุม").click().run()
        self.assertEqual([e.value for e in at.exception], [])
        created = service.list_meetings(self.user)
        self.assertEqual(len(created), 1)
        mid = created[0]["meeting_id"]
        self.assertEqual(db.get_meeting(mid)["title"], "ประชุมที่สร้างจากฟอร์ม")
        self.assertEqual(db.get_meeting(mid)["status"], "scheduled")
        self.assertIn("ประชุมที่สร้างจากฟอร์ม", at.title[0].value)

    def test_creating_with_an_existing_link_asks_before_making_a_duplicate(self):
        existing = self.meeting("scheduled")
        at = self.app(view="new")
        at.text_input[0].set_value(URL)
        next(b for b in at.button if b.label == "สร้างการประชุม").click().run()
        self.assertTrue(any("มีการประชุมที่ใช้ลิงก์" in v for v in texts(at.warning)))
        before = len(service.list_meetings(self.user))
        next(b for b in at.button if "ยกเลิก" in b.label).click().run()
        self.assertEqual(len(service.list_meetings(self.user)), before)       # ยังไม่สร้าง
        # เลือก "เปิดอันเดิม"
        at = self.app(view="new")
        at.text_input[0].set_value(URL)
        next(b for b in at.button if b.label == "สร้างการประชุม").click().run()
        button(at, "เปิดอันนี้").click().run()
        self.assertIn("ประชุมทดสอบหน้าเว็บ", at.title[0].value)          # เปิดการประชุมเดิมแทนการสร้างซ้ำ
        self.assertEqual(len(service.list_meetings(self.user)), before)

    # ── หน้าการประชุมทุกสถานะ ต้องเรนเดอร์ได้และบอกขั้นตอนต่อไปถูก ──

    def test_every_status_renders_with_the_right_next_step(self):
        expect = {
            "scheduled": "เริ่มบอทเข้าห้องประชุม", "recording": "บอทกำลังบันทึกการประชุม",
            "transcript_review": "ตรวจทานข้อความที่บอทจับได้", "transcript_verified": "ให้ AI ร่างรายงานการประชุม",
            "draft": "ตรวจ แก้ไข และอนุมัติรายงาน", "approved": "เสร็จแล้ว",
        }
        for status, step in expect.items():
            with self.subTest(status=status):
                mid = self.meeting(status)
                at = self.app(m=mid)
                self.assertIn("ประชุมทดสอบหน้าเว็บ", at.title[0].value)
                self.assertIn(step, " ".join(texts(at.info) + texts(at.success)))
                self.assertEqual(len(at.tabs), 4)

    def test_cannot_open_someone_elses_meeting(self):
        other = db.upsert_user("dev:other@x.com", "other@x.com", "คนอื่น", None)
        mid = db.create_meeting_setup(other, meet_url=URL, title="ของคนอื่น")
        at = self.app(m=mid)
        self.assertTrue(any("ไม่พบการประชุมนี้" in v for v in texts(at.error)))
        self.assertNotIn("ของคนอื่น", " ".join(texts(at.title)))

    # ── บอท ──

    def test_bot_tab_when_the_bot_service_is_down_explains_how_to_start_it(self):
        self.bot["health"] = None
        mid = self.meeting("scheduled")
        at = self.app(m=mid)
        self.assertTrue(any("บริการบอทไม่ได้เปิดอยู่" in v for v in texts(at.error)))
        self.assertFalse(any("เริ่มบอท" in b.label for b in at.button if b.key and b.key.startswith("start_")))
        self.assertTrue(any("บริการบอทไม่ได้เปิดอยู่" in v for v in texts(at.sidebar.error)))

    def test_start_button_calls_the_bot_service_for_this_meeting(self):
        mid = self.meeting("scheduled")
        at = self.app(m=mid)
        next(b for b in at.button if b.key == f"start_{mid}").click().run()
        self.assertEqual(self.calls, [("start", mid)])

    def test_bot_start_error_message_is_shown(self):
        mid = self.meeting("scheduled")

        def refuse(user, meeting_id):
            raise botclient.BotError("บอทกำลังบันทึกการประชุมอื่นอยู่ — หยุดอันนั้นก่อน")
        with mock.patch.object(botclient, "start", refuse):
            at = self.app(m=mid)
            next(b for b in at.button if b.key == f"start_{mid}").click().run()
        self.assertTrue(any("หยุดอันนั้นก่อน" in v for v in texts(at.error)))

    def test_resume_is_offered_when_the_bot_dropped_mid_meeting(self):
        mid = self.meeting("recording")                       # สถานะ recording แต่บอทไม่ได้ทำงาน
        at = self.app(m=mid)
        self.assertTrue(any("บอทไม่ได้ทำงานอยู่" in v for v in texts(at.warning)))
        self.assertIn("▶ กลับไปบันทึกต่อ", [b.label for b in at.button])

    def test_running_here_shows_stop_and_live_captions(self):
        mid = self.meeting("recording")
        self.bot["state"] = {"running": True, "mine": True, "meeting_id": mid, "error": None,
                             "status": [{"t": "10:00:00", "text": "เข้าห้องแล้ว"}],
                             "rows": [{"id": 1, "name": "Alice", "text": "สวัสดี *ตัวหนา* $x$", "final": True},
                                      {"id": 2, "name": "Bob", "text": "กำลังพูด", "final": False}]}
        at = self.app(m=mid)
        self.assertTrue(any("หยุดบอท" in b.label for b in at.button))
        live = " ".join(texts(at.markdown))
        self.assertIn("Alice", live)
        self.assertIn(r"\*ตัวหนา\*", live)                    # อักขระ Markdown จากคำบรรยายถูก escape ไม่ถูกตีความ
        next(b for b in at.button if "หยุดบอท" in b.label).click().run()
        self.assertEqual(self.calls, [("stop",)])

    def test_another_meeting_running_blocks_start_with_an_explanation(self):
        mid = self.meeting("scheduled")
        self.bot["state"] = {**IDLE, "running": True, "mine": False}
        at = self.app(m=mid)
        self.assertTrue(any("บอทกำลังบันทึกการประชุมอื่นอยู่" in v for v in texts(at.warning)))
        self.assertFalse(any(b.key == f"start_{mid}" for b in at.button))

    # ── transcript ──

    def test_transcript_tab_offers_merging_unregistered_names_and_verify(self):
        mid = self.meeting("transcript_review")
        s = db.get_or_create_speaker(mid, "46 Alice Wonderland")             # Meet แสดงชื่อพ่วงเลข ไม่ตรงรายชื่อ
        db.insert_segment(mid, s, 3, "ผมคือ Alice เอง")
        at = self.app(m=mid)
        self.assertTrue(any("ไม่ตรงกับรายชื่อ" in v for v in texts(at.subheader)))
        select = next(x for x in at.selectbox if x.key == f"merge_to_{s}")
        alice = next(p for p in db.list_speakers(mid) if p["display_name"] == "Alice")["speaker_id"]
        select.select(alice).run()
        next(b for b in at.button if b.key == f"merge_{s}").click().run()
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        self.assertNotIn("46 Alice Wonderland", people)
        self.assertEqual(people["Alice"]["segment_count"], 2)                  # ข้อความย้ายไปอยู่กับ Alice
        self.assertEqual(people["Alice"]["meet_alias"], "46 Alice Wonderland")

        at = self.app(m=mid)
        next(b for b in at.button if b.key == f"verify_{mid}").click().run()
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")

    def test_transcript_is_read_only_after_verification_until_reopened(self):
        mid = self.meeting("transcript_verified")
        at = self.app(m=mid)
        self.assertFalse(any(b.key == f"verify_{mid}" for b in at.button))
        self.assertTrue(any(b.key == f"reopen_{mid}" for b in at.button))
        self.assertTrue(any("ยืนยัน transcript แล้ว" in v for v in texts(at.success)))

    # ── รายงาน ──

    def test_report_tab_generate_then_shows_draft_with_warnings_and_actions(self):
        mid = self.meeting("transcript_verified")
        at = self.app(m=mid)
        next(b for b in at.button if b.key == f"gen_{mid}").click().run()
        self.assertEqual(db.get_meeting(mid)["status"], "draft")
        at = self.app(m=mid)
        self.assertTrue(any("รายงานฉบับร่าง" in v for v in texts(at.warning)))
        self.assertTrue(any("ผู้รับผิดชอบ" in w and "ใครสักคน" in w for e in at.expander for w in texts(e.markdown)))   # คำเตือนจาก AI
        self.assertIn("งบประมาณ", [t.value for t in at.text_input if t.key and t.key.startswith("ag_t_")])
        self.assertTrue(any(b.key == f"approve_{mid}" for b in at.button))

    def test_approved_report_is_locked_and_offers_reopen_and_calendar(self):
        mid = self.meeting("approved")
        at = self.app(m=mid)
        self.assertTrue(any("อนุมัติแล้วโดย" in v for v in texts(at.success)))
        self.assertTrue(all(t.disabled for t in at.text_input if t.key and t.key.startswith("ag_t_")))
        self.assertFalse(any(b.key == f"approve_{mid}" for b in at.button))
        self.assertTrue(any(b.key == f"reopen_rep_{mid}" for b in at.button))

    def test_calendar_card_shows_clear_sent_status(self):
        """กดส่งเข้า Calendar แล้วต้องเห็นสถานะ "ส่งเรียบร้อยแล้ว" ในการ์ดเอง และเห็นต่อเนื่องเมื่อเปิดหน้าใหม่"""
        import calendar_sync
        mid = self.meeting("approved")               # fake_ai: งาน 1 มีวันที่, งาน 2 ไม่มีวันที่
        sent = []

        def fake_sync(item_id, user_id, service=None):
            sent.append(item_id)
            db.mark_action_item_synced(item_id, "evt-" + str(item_id))
            return "evt"

        with mock.patch.object(service, "calendar_connected", lambda user: True), \
                mock.patch.object(calendar_sync, "sync_action_item", fake_sync):
            at = self.app(m=mid)
            self.assertTrue(any("ยังไม่ได้ส่งงานเข้า Calendar" in v for v in texts(at.caption)))
            self.assertTrue(any("ไม่ได้ส่ง (ไม่มีวันที่กำหนด)" in v for v in texts(at.caption)))
            button(at, "ส่งงานทั้งหมดเข้า Calendar").click().run()
            self.assertEqual(len(sent), 1)                                        # ส่งเฉพาะงานที่มีวันที่
            self.assertTrue(any("ส่งเข้า Google Calendar เรียบร้อยแล้ว 1 รายการ" in v for v in texts(at.success)))
            self.assertTrue(any("ส่งเข้า Google Calendar แล้วครบ 1/1" in v for v in texts(at.success)))
            self.assertTrue(button(at, "ส่งงานทั้งหมดเข้า Calendar").disabled)  # ส่งครบแล้ว กดซ้ำไม่ได้
            at = self.app(m=mid)                                                  # เปิดหน้าใหม่ภายหลัง ยังเห็นสถานะ
            self.assertTrue(any("แล้วครบ 1/1" in v for v in texts(at.success)))

    def test_calendar_card_reports_failures_clearly(self):
        import calendar_sync
        mid = self.meeting("approved")

        def broken(item_id, user_id, service=None):
            raise RuntimeError("Google ปฏิเสธคำขอ")

        with mock.patch.object(service, "calendar_connected", lambda user: True), \
                mock.patch.object(calendar_sync, "sync_action_item", broken):
            at = self.app(m=mid)
            button(at, "ส่งงานทั้งหมดเข้า Calendar").click().run()
            self.assertTrue(any("ส่งไม่สำเร็จ" in v and "Google ปฏิเสธคำขอ" in v for v in texts(at.error)))
            self.assertFalse(any("เรียบร้อยแล้ว" in v for v in texts(at.success)))   # ไม่บอกว่าสำเร็จถ้าไม่สำเร็จ

    def test_after_editing_transcript_of_an_approved_report_ai_can_regenerate(self):
        """อนุมัติแล้ว -> ยกเลิกอนุมัติ -> กลับไปแก้ transcript -> ยืนยันใหม่ ต้องมีปุ่มให้ AI ร่างใหม่ (เคยไม่มีปุ่ม ค้างอยู่)"""
        mid = self.meeting("approved")
        service.reopen_report(self.user, mid)
        service.reopen_transcript(self.user, mid)
        service.verify_transcript(self.user, mid)
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")
        at = self.app(m=mid)
        self.assertTrue(any("แก้และยืนยัน transcript ใหม่แล้ว" in v for v in texts(at.info)))
        regen = next(b for b in at.button if b.key == f"regen_{mid}")
        self.assertFalse(any(b.key == f"approve_{mid}" for b in at.button))     # ยังไม่ให้อนุมัติฉบับเก่า
        regen.click().run()                                                       # เปิด dialog ยืนยัน
        self.assertEqual([e.value for e in at.exception], [])
        service.generate_report(self.user, mid, generate=fake_ai)                 # สิ่งที่ปุ่มใน dialog เรียก
        self.assertEqual(db.get_meeting(mid)["status"], "draft")

    def test_dev_banner_is_always_visible_in_test_mode(self):
        at = self.app()
        self.assertTrue(any("โหมดทดสอบ" in v for v in texts(at.sidebar.warning)))


class TableConversionTests(unittest.TestCase):
    """ตารางที่แก้ในหน้าเว็บ (st.data_editor) -> ข้อมูลที่ส่งเข้า service ป้อนค่าแบบที่ pandas ส่งกลับมาจริง"""

    def test_people_table_maps_labels_and_handles_blank_and_nan(self):
        import pandas as pd

        from ui import tab_setup
        df = pd.DataFrame([
            {"speaker_id": 5, "ชื่อ": "  สมชาย  ", "อีเมล": "a@x.com", "บทบาท": "ประธาน", "การเข้าร่วม": "เข้าร่วม", "ที่มา": "x", "ข้อความที่พูด": 2},
            {"speaker_id": float("nan"), "ชื่อ": "คนใหม่", "อีเมล": None, "บทบาท": None, "การเข้าร่วม": None, "ที่มา": None, "ข้อความที่พูด": None},
        ])
        rows = tab_setup.people_rows(df)
        self.assertEqual(rows[0], {"speaker_id": 5, "display_name": "สมชาย", "email": "a@x.com", "role": "chair", "attendance": "present"})
        self.assertEqual(rows[1], {"speaker_id": None, "display_name": "คนใหม่", "email": "", "role": "attendee", "attendance": "invited"})

    def test_new_meeting_people_skip_blank_rows(self):
        import pandas as pd

        from ui import new_meeting
        df = pd.DataFrame([{"ชื่อ": "", "อีเมล": "", "บทบาท": "ประธาน", "การเข้าร่วม": "เข้าร่วม"},
                           {"ชื่อ": "Bob", "อีเมล": "", "บทบาท": "เลขา", "การเข้าร่วม": "ไม่มา"}])
        self.assertEqual(new_meeting.people_from_df(df),
                         [{"display_name": "Bob", "email": None, "role": "secretary", "attendance": "absent"}])

    def test_segment_table_rows(self):
        import pandas as pd

        from ui import tab_transcript
        df = pd.DataFrame([
            {"segment_id": 3, "เวลา": "10:00:00", "ผู้พูด": "Alice", "ข้อความ": " สวัสดี ", "ลบ": True, "แก้แล้ว": ""},
            {"segment_id": float("nan"), "เวลา": "", "ผู้พูด": "x", "ข้อความ": "ไม่มีรหัส", "ลบ": False, "แก้แล้ว": ""},
        ])
        self.assertEqual(tab_transcript.segment_rows(df),
                         [{"segment_id": 3, "display_name": "Alice", "text": "สวัสดี", "deleted": True}])

    def test_action_items_roundtrip_through_the_table(self):
        import datetime

        import pandas as pd

        from ui import tab_report
        items = [{"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-09", "due_time": "13:00",
                  "due_time_end": None, "evidence": ["จะส่ง"], "grounded": True, "calendar_synced": True},
                 {"description": "ไม่มีผู้รับผิดชอบ", "assignee": None, "due_date": None, "due_time": None,
                  "due_time_end": None, "evidence": [], "grounded": None}]
        df = tab_report.actions_df(items)
        self.assertEqual(df.loc[0, "วันที่"], datetime.date(2026, 10, 9))
        self.assertEqual(df.loc[0, "Calendar"], "✓ ส่งแล้ว")
        self.assertIn("⚠ ไม่มีข้อความอ้างอิง", df.loc[1, "หลักฐานจาก transcript"])
        back = tab_report.actions_from_df(df)
        self.assertEqual([(b["description"], b["assignee"], b["due_date"], b["due_time"], b["due_time_end"], b["evidence"]) for b in back],
                         [("ส่งรายงาน", "Alice", "2026-10-09", "13:00", None, ["จะส่ง"]),
                          ("ไม่มีผู้รับผิดชอบ", None, None, None, None, [])])

    def test_action_table_values_as_the_browser_returns_them(self):
        """ค่าจากตารางจริง: Timestamp/NaT/NaN/time/สตริงว่าง ต้องไม่ทำให้พังและไม่หลุดเป็นขยะ"""
        import datetime

        import pandas as pd

        from ui import tab_report
        df = pd.DataFrame([
            {"_ev": None, "งาน / นัดหมาย": "งานที่เพิ่มใหม่", "ผู้รับผิดชอบ": None, "วันที่": pd.Timestamp("2026-11-02"),
             "เวลาเริ่ม": datetime.time(9, 5), "เวลาสิ้นสุด": pd.NaT, "หลักฐานจาก transcript": None, "Calendar": None},
            {"_ev": "[]", "งาน / นัดหมาย": "   ", "ผู้รับผิดชอบ": "— ไม่ระบุ —", "วันที่": None, "เวลาเริ่ม": None,
             "เวลาสิ้นสุด": None, "หลักฐานจาก transcript": "", "Calendar": ""},
            {"_ev": "ไม่ใช่ json", "งาน / นัดหมาย": "evidence พัง", "ผู้รับผิดชอบ": "— ไม่ระบุ —", "วันที่": float("nan"),
             "เวลาเริ่ม": float("nan"), "เวลาสิ้นสุด": float("nan"), "หลักฐานจาก transcript": "", "Calendar": ""},
        ])
        out = tab_report.actions_from_df(df)
        self.assertEqual(len(out), 2)                                   # แถวที่ไม่มีคำอธิบายถูกข้าม
        self.assertEqual((out[0]["due_date"], out[0]["due_time"], out[0]["due_time_end"], out[0]["assignee"], out[0]["evidence"]),
                         ("2026-11-02", "09:05", None, None, []))
        self.assertEqual((out[1]["due_date"], out[1]["due_time"], out[1]["evidence"]), (None, None, []))

    def test_markdown_escape_neutralises_formatting_and_math(self):
        from ui import common
        self.assertEqual(common.md_escape("a*b_c$d"), r"a\*b\_c\$d")
        self.assertEqual(common.md_escape(None), "")


if __name__ == "__main__":
    unittest.main()
