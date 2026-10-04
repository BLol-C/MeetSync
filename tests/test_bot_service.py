"""
ทดสอบบริการบอท (app.py) ผ่าน HTTP จริงด้วย TestClient กับ MySQL ชั่วคราว — ใช้เอนจินปลอมแทน Chrome/Meet
พิสูจน์: ต้องมีโทเคน, สิทธิ์ตามรายการประชุม, บันทึกคำบรรยายลง DB ไม่ซ้ำ, บอทจบเองแล้วปิดประชุมให้, บันทึกต่อได้, OAuth ของ Calendar
"""

import asyncio
import os
import time
import unittest
from unittest import mock

from tests.dbcase import TempDbCase

os.environ.setdefault("BOT_API_TOKEN", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

import app as botapp  # noqa: E402
import botclient  # noqa: E402
import db  # noqa: E402

URL = "https://meet.google.com/abc-defg-hij"


class FakeEngine:
    """แทน MeetCaptionEngine: ส่ง event ตาม script แล้วรอสั่งหยุด (หรือจบเองถ้า auto_end)"""

    script: list = []
    auto_end = False

    def __init__(self, on_event, max_duration_s=None):
        self.on_event = on_event
        self._stop = asyncio.Event()
        self._task = None

    def start(self, url):
        self.url = url
        self._task = asyncio.create_task(self._run())
        return self._task

    def is_running(self):
        return self._task is not None and not self._task.done()

    async def stop(self):
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self):
        try:
            for ev in FakeEngine.script:
                self.on_event(ev)
                await asyncio.sleep(0.01)
            if not FakeEngine.auto_end:
                await self._stop.wait()
        finally:
            self.on_event({"type": "ended"})


def cap(row_id, name, text, final=True):
    return {"type": "caption", "id": row_id, "name": name, "text": text, "final": final}


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class BotServiceTests(TempDbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.patch = mock.patch.object(botapp, "MeetCaptionEngine", FakeEngine)
        cls.patch.start()
        cls.ctx = TestClient(botapp.app)
        cls.client = cls.ctx.__enter__()      # เปิด lifespan (init_schema บนฐานชั่วคราว) และคงลูปเดียวตลอดคลาส
        mk = lambda sub, email: {"user_id": db.upsert_user(sub, email, email, None), "email": email}  # noqa: E731
        cls.owner, cls.other = mk("bot-owner", "owner@x.com"), mk("bot-other", "other@x.com")

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)
        cls.patch.stop()
        super().tearDownClass()

    def tearDown(self):
        FakeEngine.script, FakeEngine.auto_end = [], False
        self.client.post("/bot/stop", headers=botclient.headers_for(self.owner))

    def h(self, user):
        return botclient.headers_for(user)

    def meeting(self, user=None, people=("Alice",)):
        user = user or self.owner
        mid = db.create_meeting_setup(user["user_id"], meet_url=URL, title="ทดสอบบอท")
        for p in people:
            db.add_speaker(mid, p)
        return mid

    def start(self, mid, user=None):
        return self.client.post("/bot/start", json={"meeting_id": mid}, headers=self.h(user or self.owner))

    def state(self, user=None):
        return self.client.get("/bot/state", headers=self.h(user or self.owner)).json()

    # ── สิทธิ์ ──

    def test_requests_without_the_secret_token_are_rejected(self):
        self.assertEqual(self.client.get("/bot/state").status_code, 401)
        self.assertEqual(self.client.post("/bot/start", json={"meeting_id": 1}).status_code, 401)
        bad = {**self.h(self.owner), "X-Bot-Token": "wrong"}
        self.assertEqual(self.client.get("/bot/state", headers=bad).status_code, 401)
        self.assertEqual(self.client.post("/bot/stop", headers=bad).status_code, 401)
        self.assertEqual(self.client.get("/bot/state", headers={"X-Bot-Token": "test-token"}).status_code, 401)   # ไม่ระบุผู้ใช้
        self.assertEqual(self.client.get("/health").json()["ok"], True)

    def test_start_validation(self):
        self.assertEqual(self.start(99999).status_code, 404)                       # ไม่มีการประชุมนี้
        mid = self.meeting()
        self.assertEqual(self.start(mid, self.other).status_code, 404)             # ของคนอื่น: ไม่เปิดเผยว่ามีอยู่
        db.begin_recording(mid)
        db.end_meeting(mid)
        db.set_meeting_status(mid, "transcript_verified")
        r = self.start(mid)
        self.assertEqual(r.status_code, 409)                                       # ผ่านขั้นตรวจแล้วบันทึกต่อไม่ได้
        self.assertIn("ตรวจทาน", r.json()["detail"])
        self.assertFalse(self.state()["running"])

    def test_chair_by_email_can_start_and_stop(self):
        mid = self.meeting(people=())
        db.add_speaker(mid, "ประธาน", email="chair@x.com", role="chair")
        chair = {"user_id": db.upsert_user("bot-chair", "chair@x.com", "ประธาน", None), "email": "chair@x.com"}
        self.assertEqual(self.start(mid, chair).status_code, 200)
        self.assertEqual(self.client.post("/bot/stop", headers=self.h(chair)).status_code, 200)
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_review")

    # ── บันทึกคำบรรยาย ──

    def test_full_run_saves_captions_without_duplicates(self):
        mid = self.meeting()
        FakeEngine.script = [
            {"type": "status", "text": "เข้าห้องแล้ว"},
            cap(1, "Alice (You)", "สวัสดี", final=False),          # ยังไม่ final: แสดงสดแต่ยังไม่บันทึก
            cap(1, "Alice (You)", "สวัสดี"),
            cap(1, "Alice (You)", "สวัสดีครับทุกคน"),              # แถวเดิม final ซ้ำด้วยข้อความที่ยาวขึ้น -> อัปเดตแถวเดิม
            cap(2, "คนแปลกหน้า", "ขอเข้าร่วมด้วย"),
            cap(3, "(raw)", "ข้อความดิบ"),                         # ชื่อ (raw) ต้องไม่ถูกบันทึก
        ]
        r = self.start(mid)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(wait_for(lambda: len(db.get_transcript(mid)) == 2), db.get_transcript(mid))

        live = self.state()
        self.assertTrue(live["running"] and live["mine"])
        self.assertEqual(live["meeting_id"], mid)
        self.assertEqual([x["name"] for x in live["rows"]], ["Alice (You)", "คนแปลกหน้า"])
        self.assertEqual(live["rows"][0]["text"], "สวัสดีครับทุกคน")
        self.assertTrue(any("เข้าห้องแล้ว" in s["text"] for s in live["status"]))
        self.assertEqual(db.get_meeting(mid)["status"], "recording")

        self.assertEqual(self.client.post("/bot/stop", headers=self.h(self.owner)).status_code, 200)
        self.assertEqual(db.get_meeting(mid)["status"], "transcript_review")
        self.assertIsNotNone(db.get_meeting(mid)["ended_at"])
        texts = [(t["display_name"], t["text"]) for t in db.get_transcript(mid)]
        self.assertEqual(texts, [("Alice", "สวัสดีครับทุกคน"), ("คนแปลกหน้า", "ขอเข้าร่วมด้วย")])   # ไม่ซ้ำ + "(You)" ถูกจับคู่
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        self.assertEqual(people["Alice"]["attendance"], "present")
        self.assertEqual(people["คนแปลกหน้า"]["source"], "meet")
        after = self.state()
        self.assertFalse(after["running"])
        self.assertEqual(len(after["rows"]), 2)              # ผู้จัดการยังเห็นผลของรอบล่าสุดหลังหยุด

    def test_one_endless_monologue_is_split_into_segments_not_lost(self):
        # คนพูดไม่หยุด Meet ใช้แถวเดียวตลอด ข้อความสะสมยาวเกินคอลัมน์ได้ — ต้องแบ่งเป็นหลาย segment ครบ ไม่ error ไม่หาย
        mid = self.meeting()
        word = "สวัสดีครับทุกคน "
        part1 = word * 150                    # ~2,400 ตัวอักษร: ยังไม่เกินเพดาน
        long_text = word * 600                # ~9,600 ตัวอักษร: เกินเพดาน 3,000 ตัวอักษรหลายรอบ
        FakeEngine.script = [cap(1, "Alice", part1), cap(1, "Alice", long_text), cap(1, "Alice", long_text + "จบ")]
        self.assertEqual(self.start(mid).status_code, 200)
        self.assertTrue(wait_for(lambda: len(db.get_transcript(mid)) >= 4), len(db.get_transcript(mid)))
        self.client.post("/bot/stop", headers=self.h(self.owner))
        segs = db.get_transcript(mid)
        self.assertTrue(all(len(t["text"]) <= botapp._SEGMENT_MAX_CHARS for t in segs))
        self.assertEqual("".join(t["text"] for t in segs), long_text + "จบ")      # ต่อกันได้เท่าเดิมเป๊ะ ไม่ซ้ำไม่หาย
        self.assertEqual([s["sequence_no"] for s in db.list_segments(mid)], list(range(1, len(segs) + 1)))
        self.assertFalse(any("ไม่สำเร็จ" in s["text"] for s in self.state()["status"]))

    def test_bot_ending_by_itself_closes_the_meeting(self):
        mid = self.meeting()
        FakeEngine.script, FakeEngine.auto_end = [cap(1, "Alice", "พูดแล้วประชุมจบ")], True
        self.assertEqual(self.start(mid).status_code, 200)
        self.assertTrue(wait_for(lambda: db.get_meeting(mid)["status"] == "transcript_review"), db.get_meeting(mid))
        self.assertEqual([t["text"] for t in db.get_transcript(mid)], ["พูดแล้วประชุมจบ"])   # คิวที่ค้างถูกเขียนครบก่อนปิด
        self.assertFalse(self.state()["running"])
        self.assertEqual(self.start(mid).status_code, 200)    # และกลับมาเริ่มรอบใหม่ได้ ไม่ติดสถานะค้าง

    def test_resume_continues_sequence_numbers(self):
        mid = self.meeting()
        FakeEngine.script = [cap(1, "Alice", "รอบแรก")]
        self.start(mid)
        self.assertTrue(wait_for(lambda: len(db.get_transcript(mid)) == 1))
        self.client.post("/bot/stop", headers=self.h(self.owner))
        started = db.get_meeting(mid)["started_at"]

        FakeEngine.script = [cap(1, "Alice", "รอบสองหลังบอทหลุด")]      # เลขแถวฝั่ง JS เริ่มใหม่ ต้องไม่ไปทับของเดิม
        self.assertEqual(self.start(mid).status_code, 200)
        self.assertTrue(wait_for(lambda: len(db.get_transcript(mid)) == 2))
        self.client.post("/bot/stop", headers=self.h(self.owner))
        self.assertEqual([t["text"] for t in db.get_transcript(mid)], ["รอบแรก", "รอบสองหลังบอทหลุด"])
        self.assertEqual([s["sequence_no"] for s in db.list_segments(mid)], [1, 2])
        self.assertEqual(db.get_meeting(mid)["started_at"], started)           # เวลาเริ่มเดิมคงไว้

    # ── ใช้บอทพร้อมกัน ──

    def test_only_one_meeting_at_a_time_and_others_cannot_see_or_stop_it(self):
        a, b = self.meeting(), self.meeting()
        other_meeting = self.meeting(self.other)
        FakeEngine.script = [cap(1, "Alice", "ความลับของห้อง A")]
        self.assertEqual(self.start(a).status_code, 200)
        self.assertTrue(wait_for(lambda: self.state()["rows"]))

        self.assertEqual(self.start(a).status_code, 409)                           # เริ่มซ้ำห้องเดิม
        r = self.start(b)
        self.assertEqual(r.status_code, 409)                                       # เริ่มอีกห้องขณะรันอยู่
        self.assertIn("อื่น", r.json()["detail"])
        r = self.start(other_meeting, self.other)                                  # ผู้ใช้อื่นเริ่มห้องของตัวเองก็ไม่ได้
        self.assertEqual(r.status_code, 409)

        seen = self.state(self.other)
        self.assertEqual((seen["running"], seen["mine"], seen["rows"], seen["status"], seen["meeting_id"]),
                         (True, False, [], [], None))                              # รู้แค่ว่าบอทไม่ว่าง ไม่เห็นเนื้อหา
        self.assertEqual(self.client.post("/bot/stop", headers=self.h(self.other)).status_code, 403)
        self.assertTrue(self.state()["running"])                                   # ยังรันอยู่ ไม่ถูกหยุดโดยคนอื่น

    def test_state_after_a_run_is_hidden_from_non_managers(self):
        mid = self.meeting()
        FakeEngine.script = [cap(1, "Alice", "เนื้อหาลับ"), {"type": "error", "text": "เกิดข้อผิดพลาดบางอย่าง"}]
        self.start(mid)
        self.assertTrue(wait_for(lambda: self.state()["error"]))
        self.client.post("/bot/stop", headers=self.h(self.owner))
        mine, theirs = self.state(), self.state(self.other)
        self.assertEqual(mine["error"], "เกิดข้อผิดพลาดบางอย่าง")
        self.assertEqual((theirs["running"], theirs["rows"], theirs["status"], theirs["error"]), (False, [], [], None))

    # ── Calendar OAuth ──

    def test_calendar_connect_requires_a_valid_signed_token(self):
        self.assertEqual(self.client.get("/calendar/connect", params={"t": "garbage"}).status_code, 400)
        token = botclient.calendar_connect_token(self.owner["user_id"], botclient.UI_URL + "/?m=1")
        with mock.patch.object(botapp.calendar_auth, "build_auth_url", lambda state, login_hint=None: f"https://accounts.google.com/o?state={state}"):
            r = self.client.get("/calendar/connect", params={"t": token}, follow_redirects=False)
        self.assertIn(r.status_code, (302, 307))
        self.assertTrue(r.headers["location"].startswith("https://accounts.google.com/"))
        expired = botclient._signer().dumps({"uid": 1, "ret": botclient.UI_URL})
        with mock.patch.object(botclient, "read_calendar_token", side_effect=botapp.SignatureExpired("old")):
            self.assertEqual(self.client.get("/calendar/connect", params={"t": expired}).status_code, 400)

    def test_calendar_callback_saves_token_for_the_signed_user_and_returns_only_to_our_ui(self):
        saved = []
        with mock.patch.object(botapp.calendar_auth, "exchange_code", lambda code, uid: saved.append((code, uid))):
            good = botclient.calendar_connect_token(self.owner["user_id"], botclient.UI_URL + "/?m=7")
            r = self.client.get("/auth/google/callback", params={"code": "abc", "state": good}, follow_redirects=False)
            self.assertEqual(r.headers["location"], botclient.UI_URL + "/?m=7")
            evil = botclient.calendar_connect_token(self.owner["user_id"], "https://evil.example/steal")
            r = self.client.get("/auth/google/callback", params={"code": "abc", "state": evil}, follow_redirects=False)
            self.assertEqual(r.headers["location"], botclient.UI_URL)                    # ปลายทางนอกระบบ -> กลับหน้าหลัก
            self.assertEqual(self.client.get("/auth/google/callback", params={"code": "abc", "state": "forged"}).status_code, 400)
            self.assertEqual(self.client.get("/auth/google/callback", params={"state": good}).status_code, 400)   # ไม่มี code
        self.assertEqual(saved, [("abc", self.owner["user_id"]), ("abc", self.owner["user_id"])])


class TokenTests(unittest.TestCase):
    def test_token_prefers_env_then_file(self):
        with mock.patch.dict(os.environ, {"BOT_API_TOKEN": "from-env"}):
            self.assertEqual(botclient.get_token(), "from-env")

    def test_token_file_is_created_once_and_reused(self):
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"BOT_API_TOKEN": ""}):
            path = pathlib.Path(d) / ".bot_token"
            with mock.patch.object(botclient, "TOKEN_FILE", path):
                first = botclient.get_token()
                self.assertEqual(len(first), 64)
                self.assertEqual(botclient.get_token(), first)
                self.assertEqual(path.read_text(), first)


if __name__ == "__main__":
    unittest.main()
