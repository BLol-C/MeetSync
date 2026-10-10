"""
ทดสอบหน้าเว็บ Streamlit ด้วย AppTest (รันสคริปต์จริงโดยไม่ต้องมีเบราว์เซอร์) กับ MySQL ชั่วคราว
ใช้ผู้ใช้ทดสอบ (MEETSYNC_DEV_USER) และบริการบอท/AI ปลอม — พิสูจน์ว่าทุกสถานะของการประชุมเรนเดอร์ได้ไม่พัง
และปุ่มสำคัญเรียก service/บอทถูกที่ (การคลิกในตารางแก้ไขต้องดูด้วยเบราว์เซอร์: tools/ui_smoke.py)
"""

import datetime
import os
import unittest
from unittest import mock

from tests.dbcase import ROOT, TempDbCase

os.environ["MEETSYNC_DEV_USER"] = "owner@x.com|เจ้าของ"
os.environ.setdefault("BOT_API_TOKEN", "test-token")

from streamlit.testing.v1 import AppTest  # noqa: E402

from bot import botclient  # noqa: E402
import db  # noqa: E402
import service  # noqa: E402
from ui import common  # noqa: E402
from reports import summarizer  # noqa: E402
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

    def test_draft_report_has_a_section_selector_per_agenda_item_and_setup_offers_previous_meetings(self):
        prev = self.meeting("approved")
        mid = self.meeting("draft")
        at = self.app(m=mid)
        section_boxes = [x for x in at.selectbox if x.key.startswith(f"ag_s_{mid}_")]
        self.assertTrue(section_boxes)                                                    # ทุกเรื่องในรายงานเลือกวาระได้
        self.assertEqual(section_boxes[0].value, "วาระ 4.2 · เรื่องพิจารณาใหม่")           # ที่ AI สรุปเริ่มที่ 4.2
        previous_box = next(x for x in at.selectbox if x.key == f"prev_{mid}")
        self.assertIn(f"(#{prev})", " ".join(previous_box.options))                        # เสนอเฉพาะการประชุมที่อนุมัติแล้ว
        self.assertEqual(previous_box.value, "— ไม่มี / ไม่ใช้ข้อมูลครั้งก่อน —")
        new = self.app(view="new")
        new_box = next(x for x in new.selectbox if x.key == "new_previous_meeting")
        self.assertEqual(new_box.value, "— ไม่มี / ไม่ใช้ข้อมูลครั้งก่อน —")              # หน้าสร้างใหม่เริ่มที่ "ไม่ใช้" แม้มีครั้งที่อนุมัติแล้ว ผู้ใช้เลือกเอง
        self.assertIn(new_box.value, new_box.options)

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

    def test_every_status_renders_the_meeting_page_with_status_line_and_four_tabs(self):
        for status in ("scheduled", "recording", "transcript_review", "transcript_verified", "draft", "approved"):
            with self.subTest(status=status):
                mid = self.meeting(status)
                at = self.app(m=mid)
                self.assertIn("ประชุมทดสอบหน้าเว็บ", at.title[0].value)
                self.assertEqual(len(at.tabs), 5)
                body = " ".join(texts(at.caption))
                self.assertIn(common.STATUS_LABEL[status], body)                      # บรรทัดสถานะยังอยู่ (ไม่มีลิงก์ Meet)
                self.assertNotIn("meet.google.com", body)
                self.assertFalse(any("ขั้นตอนต่อไป" in v for v in texts(at.info) + texts(at.success)))   # ไม่มีกล่องขั้นตอนต่อไปแล้ว

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

    def test_live_status_is_either_waiting_to_be_admitted_or_reading_captions(self):
        from ui import tab_bot
        line = lambda text: {"t": "10:00:00", "text": text}
        waiting = {"rows": [], "status": [line("กำลังเปิดเบราว์เซอร์…"), line("ส่งคำขอเข้าร่วมแล้ว — รอหัวหน้าห้องกดยอมรับ…")]}
        self.assertEqual(tab_bot.phase_text(waiting), "รอให้กดยอมรับเข้าห้อง")
        joined = {"rows": [], "status": waiting["status"] + [line("✅ เข้าห้องแล้วและเปิดคำบรรยายแล้ว (ตรวจยืนยันจากหน้าจอ)")]}
        self.assertEqual(tab_bot.phase_text(joined), "เข้าห้องแล้ว — กำลังอ่านคำบรรยาย (CC)")
        self.assertEqual(tab_bot.phase_text({"rows": [{"name": "A"}], "status": []}), "เข้าห้องแล้ว — กำลังอ่านคำบรรยาย (CC)")

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

    def test_unconfirmed_people_can_be_matched_to_names_seen_in_the_room_or_set_absent(self):
        mid = self.meeting("transcript_review")
        silent = service.add_person(self.user, mid, "ธนาวีร์ ผู้ฟังเงียบ")            # ลงทะเบียนไว้ แต่ไม่ได้พูด
        away = service.add_person(self.user, mid, "ศศิน ลากิจ")
        db.record_room_names(mid, ["Thanawee BOONKERD"])                              # บอทเห็นชื่อนี้ในห้อง แต่ไม่ตรงกับใคร
        room = next(p for p in db.list_speakers(mid) if p["display_name"] == "Thanawee BOONKERD")
        at = self.app(m=mid)
        self.assertTrue(any("ยังไม่ยืนยันการเข้าร่วม" in v for v in texts(at.subheader)))
        select = next(x for x in at.selectbox if x.key == f"unc_{silent}")
        self.assertTrue(any("Thanawee BOONKERD" in o for o in select.options))        # ชื่อที่เห็นในห้องอยู่ในตัวเลือก
        select.select(f"meet:{room['speaker_id']}").run()
        next(b for b in at.button if b.key == f"unc_go_{silent}").click().run()
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        self.assertNotIn("Thanawee BOONKERD", people)                                # รวมเข้ากับคนที่ลงทะเบียนแล้ว
        self.assertEqual((people["ธนาวีร์ ผู้ฟังเงียบ"]["attendance"], people["ธนาวีร์ ผู้ฟังเงียบ"]["meet_alias"]),
                         ("present", "Thanawee BOONKERD"))
        at = self.app(m=mid)                                                          # อีกคนเลือก "ไม่มา"
        next(x for x in at.selectbox if x.key == f"unc_{away}").select("absent").run()
        next(b for b in at.button if b.key == f"unc_go_{away}").click().run()
        self.assertEqual(db.get_speaker(away)["attendance"], "absent")

    def test_unmatched_meet_names_are_hidden_from_the_people_table_and_matched_from_the_transcript_tab(self):
        mid = self.meeting("transcript_review")
        s = db.get_or_create_speaker(mid, "46 Alice Wonderland")             # Meet แสดงชื่อพ่วงเลข ไม่ตรงรายชื่อ
        db.insert_segment(mid, s, 3, "ผมคือ Alice เอง")
        setup = self.app(m=mid)                                               # แท็บ ① ไม่แสดงชื่อที่พบจาก Meet ในตารางรายชื่อ
        from ui import tab_setup
        shown = [p["display_name"] for p in tab_setup.table_people(service.get_detail(self.user, mid)["people"])]
        self.assertNotIn("46 Alice Wonderland", shown)
        self.assertIn("Alice", shown)
        self.assertTrue(any("ยังจับคู่ไม่ได้" in v for v in texts(setup.subheader)))
        alice = next(p for p in db.list_speakers(mid) if p["display_name"] == "Alice")["speaker_id"]
        next(x for x in setup.selectbox if x.key == f"merge_to_{s}").select(alice).run()
        next(b for b in setup.button if b.key == f"merge_{s}").click().run()
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        self.assertNotIn("46 Alice Wonderland", people)
        self.assertEqual(people["Alice"]["meet_alias"], "46 Alice Wonderland")   # จำเป็นชื่อภาษาอังกฤษของ Alice
        self.assertEqual(people["Alice"]["segment_count"], 2)
        at = self.app(m=mid)
        next(b for b in at.button if b.key == f"verify_{mid}").click().run()
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")

    def test_transcript_tab_shows_conversation_grouped_into_speaker_turns(self):
        mid = self.meeting("transcript_review")            # สมชาย 1 ประโยค แล้ว Alice 1 ประโยค = 2 ช่วงพูด
        at = self.app(m=mid)
        self.assertTrue(any(v == "บทสนทนา" for v in texts(at.subheader)))
        body = " ".join(texts(at.markdown))
        self.assertIn("เห็นด้วยค่ะ", body)
        text = service.transcript_text(self.user, mid)
        self.assertEqual(len(text.strip().splitlines()), 2)

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

    def test_after_editing_transcript_can_keep_existing_report_and_approve(self):
        """แก้ transcript เล็กน้อยแล้วไม่ต้องร่างใหม่: ใช้รายงานเดิมต่อ แล้วอนุมัติได้เลย โดยสิ่งที่คนแก้ไว้ไม่หาย"""
        mid = self.meeting("draft")
        content = service.get_report_view(self.user, mid)["report"]["content"]
        content["summary"] = "สรุปที่คนแก้เอง"
        service.save_report_draft(self.user, mid, content)
        service.reopen_transcript(self.user, mid)
        service.verify_transcript(self.user, mid)
        at = self.app(m=mid)
        self.assertFalse(any(b.key == f"approve_{mid}" for b in at.button))        # ยังอนุมัติจากสถานะนี้ไม่ได้
        next(b for b in at.button if b.key == f"keep_{mid}").click().run()
        self.assertEqual(db.get_meeting(mid)["status"], "draft")
        self.assertEqual(db.get_report(mid)["content"]["summary"], "สรุปที่คนแก้เอง")   # ไม่ถูก AI เขียนทับ
        at = self.app(m=mid)
        self.assertTrue(any(b.key == f"approve_{mid}" for b in at.button))
        service.approve_report(self.user, mid, confirm_warnings=True)
        self.assertEqual(db.get_meeting(mid)["status"], "approved")

    def test_keep_existing_report_needs_a_report_and_verified_transcript(self):
        mid = self.meeting("transcript_verified")                                   # ยังไม่มีรายงาน
        with self.assertRaises(service.ServiceError):
            service.keep_existing_report(self.user, mid)
        mid2 = self.meeting("transcript_review")
        with self.assertRaises(service.ServiceError):
            service.keep_existing_report(self.user, mid2)

    def test_calendar_tab_waits_for_a_report_then_hosts_the_approve_button(self):
        early = self.meeting("transcript_review")
        at = self.app(m=early)
        self.assertTrue(any("ใช้ได้เมื่อมีรายงานฉบับร่างแล้ว" in v for v in texts(at.info)))
        mid = self.meeting("draft")
        at = self.app(m=mid)
        self.assertFalse(any("ใช้ได้เมื่อมีรายงานฉบับร่างแล้ว" in v for v in texts(at.info)))
        approve = [b for b in at.button if b.key == f"approve_{mid}"]
        self.assertEqual(len(approve), 1)                                      # ปุ่มอนุมัติอยู่แท็บ ⑤ แท็บเดียว ไม่ซ้ำที่ ④
        self.assertIn("ส่งเข้า Calendar", approve[0].label)

    def test_approval_from_the_calendar_tab_sends_events_and_reports_the_result(self):
        from integrations import calendar_sync
        mid = self.meeting("draft")                       # fake_ai: งาน 1 มีวันที่ งาน 2 ไม่มีวันที่
        sent = []

        def fake_create(item_id, user_id, send_invites=False, service=None):
            sent.append((item_id, send_invites))
            db.mark_action_item_synced(item_id, "evt-" + str(item_id), "https://calendar.google.com/x")
            return {"action": "created", "event_id": "evt", "link": None}

        with mock.patch.object(service, "calendar_connected", lambda user: True), \
                mock.patch.object(calendar_sync, "create_event", fake_create):
            results = service.approve_and_send(self.user, mid, confirm_warnings=True)     # เทียบเท่ากดยืนยันใน dialog
            self.assertEqual(len(sent), 1)                                                  # ส่งเฉพาะงานที่มีวันที่
            self.assertEqual(sent[0][1], False)                                             # ไม่ติ๊กส่งอีเมลเชิญ = ไม่ส่ง
            self.assertEqual([r["ok"] for r in results], [True])
            at = self.app(m=mid)
            self.assertTrue(any("อนุมัติแล้วโดย" in v for v in texts(at.success)))
            self.assertFalse(any(b.key == f"approve_{mid}" for b in at.button))             # อนุมัติแล้ว ปุ่มอนุมัติหาย
            self.assertFalse(any(b.key == f"sync_{mid}" for b in at.button))                # ส่งครบแล้ว ไม่มีปุ่มส่งซ้ำ
            invite_box = next(c for c in at.checkbox if c.key == f"cal_invites_{mid}")
            self.assertTrue(invite_box.disabled)                                           # ส่งครบแล้ว ติ๊กส่งอีเมลเชิญไม่ได้อีก
            self.assertTrue(any(b.key == f"reopen_cal_{mid}" for b in at.button))
            pdf_buttons = [d.proto.label for d in at.get("download_button") if "PDF" in d.proto.label]
            self.assertEqual(pdf_buttons, ["⬇ ดาวน์โหลด PDF ฉบับเต็ม"])                      # PDF ฉบับเต็มอยู่แท็บ ⑤ ที่เดียว ไม่ซ้ำที่ ④
        draft = self.meeting("draft")
        drafts = [d.proto.label for d in self.app(m=draft).get("download_button") if "PDF" in d.proto.label]
        self.assertEqual(drafts, ["⬇ ดาวน์โหลด PDF (ฉบับร่าง)"])                            # ฉบับร่างยังดาวน์โหลดที่แท็บ ④ เหมือนเดิม

    def test_calendar_tab_offers_retry_for_unsent_items_and_shows_failures(self):
        from integrations import calendar_sync
        mid = self.meeting("approved")                    # อนุมัติด้วย service.approve_report: ยังไม่ได้ส่งนัด

        def broken(item_id, user_id, send_invites=False, service=None):
            raise RuntimeError("Google ปฏิเสธคำขอ")

        with mock.patch.object(service, "calendar_connected", lambda user: True), \
                mock.patch.object(calendar_sync, "create_event", broken):
            at = self.app(m=mid)
            self.assertFalse(next(c for c in at.checkbox if c.key == f"cal_invites_{mid}").disabled)   # ยังมีงานค้าง ตัวเลือกยังมีผล
            button(at, "ส่งงานที่ยังไม่ส่ง (1)").click().run()
            self.assertTrue(any("ส่งไม่สำเร็จ" in v and "Google ปฏิเสธคำขอ" in v for v in texts(at.error)))
            self.assertFalse(any("ส่งเข้า Google Calendar เรียบร้อยแล้ว" in v for v in texts(at.success)))   # ไม่บอกว่าสำเร็จถ้าไม่สำเร็จ
            self.assertTrue(any(b.key == f"sync_{mid}" for b in at.button))                  # ยังกดส่งซ้ำได้

    def test_calendar_tab_table_labels_and_report_tab_tasks_are_read_only(self):
        from ui import tab_calendar
        items = [
            {"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-16", "due_time": "13:00", "due_time_end": None,
             "evidence": ["จะส่ง"], "google_calendar_event_id": "e1", "google_calendar_link": "https://calendar.google.com/x"},
            {"description": "จองห้อง", "assignee": None, "due_date": "2026-10-20", "due_time": None, "due_time_end": None, "evidence": []},
            {"description": "คิดดู", "assignee": None, "due_date": None, "due_time": None, "due_time_end": None, "evidence": []},
        ]
        df = tab_calendar.items_df(items, approved=True)
        self.assertEqual(list(df["สถานะ"]), ["✓ ส่งแล้ว", "ยังไม่ส่ง", "ไม่มีวันที่ — จะไม่ส่ง"])
        self.assertEqual(list(tab_calendar.items_df(items, approved=False)["สถานะ"])[1], "จะส่งตอนอนุมัติ")
        from ui import tab_report
        back = tab_report.actions_from_df(df)                                              # แปลงกลับเป็นงานของรายงานได้ (หลักฐานไม่หาย)
        self.assertEqual((back[0]["description"], back[0]["due_date"], back[0]["evidence"]), ("ส่งรายงาน", "2026-10-16", ["จะส่ง"]))

    def test_after_editing_transcript_of_an_approved_report_ai_can_regenerate(self):
        """อนุมัติแล้ว -> ยกเลิกอนุมัติ -> กลับไปแก้ transcript -> ยืนยันใหม่ ต้องมีปุ่มให้ AI ร่างใหม่ (เคยไม่มีปุ่ม ค้างอยู่)"""
        mid = self.meeting("approved")
        service.reopen_report(self.user, mid)
        service.reopen_transcript(self.user, mid)
        service.verify_transcript(self.user, mid)
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_verified")
        at = self.app(m=mid)
        self.assertFalse(any("แก้และยืนยัน transcript ใหม่แล้ว" in v for v in texts(at.info)))   # ไม่มีกล่องอธิบายแล้ว
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
            {"speaker_id": 5, "ชื่อภาษาไทย": "  สมชาย  ", "อีเมล": "a@x.com", "บทบาท": "ประธาน", "การเข้าร่วม": "เข้าร่วม", "สาเหตุที่ไม่มา": None, "ที่มา": "x", "ข้อความที่พูด": 2},
            {"speaker_id": float("nan"), "ชื่อภาษาไทย": "คนใหม่", "อีเมล": None, "บทบาท": None, "การเข้าร่วม": None, "สาเหตุที่ไม่มา": float("nan"), "ที่มา": None, "ข้อความที่พูด": None},
        ])
        rows = tab_setup.people_rows(df)
        self.assertEqual(rows[0], {"speaker_id": 5, "display_name": "สมชาย", "meet_alias": "", "email": "a@x.com", "role": "chair", "attendance": "present", "absence_reason": "", "position": ""})
        self.assertEqual(rows[1], {"speaker_id": None, "display_name": "คนใหม่", "meet_alias": "", "email": "", "role": "attendee", "attendance": "invited", "absence_reason": "", "position": ""})

    def test_new_meeting_people_skip_blank_rows(self):
        import pandas as pd

        from ui import new_meeting
        df = pd.DataFrame([{"ชื่อภาษาไทย": "", "อีเมล": "", "บทบาท": "ประธาน", "การเข้าร่วม": "เข้าร่วม"},
                           {"ชื่อภาษาไทย": "Bob", "อีเมล": "", "บทบาท": "เลขา", "การเข้าร่วม": "ไม่มา", "สาเหตุที่ไม่มา": "ลาป่วย", "ตำแหน่ง": " อาจารย์ "}])
        self.assertEqual(new_meeting.people_from_df(df),
                         [{"display_name": "Bob", "meet_alias": None, "email": None, "role": "secretary", "attendance": "absent", "absence_reason": "ลาป่วย", "position": "อาจารย์"}])

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

    def test_table_height_fits_the_rows_and_scrolls_inside_when_long(self):
        self.assertEqual(common.table_height(1), 73)                      # หัวตาราง + 1 แถว ไม่เหลือที่ว่างเปล่า
        self.assertGreater(common.table_height(1, spare_rows=2), common.table_height(1))
        self.assertEqual(common.table_height(500, max_px=420), 420)       # ยาวกว่านั้นเลื่อนในตาราง

    def test_progress_text_numbers_match_the_five_tabs(self):
        from ui import common
        self.assertIn("ขั้นที่ 1 จาก 5", common.progress_text("scheduled"))
        self.assertIn("ขั้นที่ 3 จาก 5", common.progress_text("transcript_review"))
        self.assertIn("ขั้นที่ 4 จาก 5", common.progress_text("draft"))
        self.assertNotIn("ขั้นที่", common.progress_text("approved"))


if __name__ == "__main__":
    unittest.main()
