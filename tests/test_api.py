"""
ทดสอบ API ของแอปแบบครบวงจรผ่าน TestClient กับ MySQL ชั่วคราว:
ตั้งค่าประชุม -> ผู้เข้าร่วม/บทบาท -> ตรวจ transcript -> AI ร่าง (ใช้ AI ปลอม) -> แก้ -> อนุมัติ -> PDF -> Calendar

ใช้ผู้ใช้ปลอมแทนการล็อกอิน Google (แทนที่ app._session_user) และไม่เรียก Gemini/Google จริง
"""

import json
import os
import unittest
from unittest import mock

from tests.dbcase import TempDbCase

os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.testclient import TestClient  # noqa: E402

import app as appmod  # noqa: E402
import db  # noqa: E402
import summarizer  # noqa: E402

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


class ApiFlowTests(TempDbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db.init_schema()
        appmod._db_ready = True
        cls.owner_id = db.upsert_user("sub-owner", "owner@x.com", "เจ้าของ", None)
        cls.other_id = db.upsert_user("sub-other", "other@x.com", "คนอื่น", None)
        cls.chair_id = db.upsert_user("sub-chair", "chair@x.com", "ประธาน", None)
        cls.client = TestClient(appmod.app)

    def as_user(self, user_id, email, name="x"):
        """จำลองว่าล็อกอินเป็นผู้ใช้นี้"""
        return mock.patch.object(appmod, "_session_user",
                                 lambda req: {"user_id": user_id, "email": email, "name": name, "picture": None})

    def owner(self):
        return self.as_user(self.owner_id, "owner@x.com", "เจ้าของ")

    def create_meeting(self, **over):
        body = {"meet_url": URL, "title": "ประชุมโครงการ", "org_name": "ภาควิชา", "meeting_no": "3/2569",
                "venue": "ห้อง 2", "participants": [
                    {"display_name": "สมชาย ใจดี", "email": "chair@x.com", "role": "chair", "attendance": "present"},
                    {"display_name": "สมหญิง", "role": "secretary", "attendance": "present"},
                    {"display_name": "Alice", "email": "alice@x.com"},
                    {"display_name": "Bob"},
                ]}
        body.update(over)
        with self.owner():
            r = self.client.post("/meetings", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["meeting_id"]

    def record_transcript(self, mid):
        """จำลองสิ่งที่บอทบันทึก แล้วจบการประชุม"""
        db.begin_recording(mid)
        s1 = db.get_or_create_speaker(mid, "สมชาย ใจดี (You)")
        s2 = db.get_or_create_speaker(mid, "Alice")
        s3 = db.get_or_create_speaker(mid, "Zed")
        db.insert_segment(mid, s1, 1, "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท")
        db.insert_segment(mid, s2, 2, "เห็นด้วยค่ะ ดิฉันจะส่งรายงานความก้าวหน้าวันศุกร์หน้า")
        db.insert_segment(mid, s3, 3, "ทดสอบไมค์ ๆ")
        db.end_meeting(mid)

    # ── ตั้งค่า / สิทธิ์ ──

    def test_unauthenticated_requests_are_rejected(self):
        for method, url in [("get", "/meetings"), ("post", "/meetings"), ("get", "/meetings/1"),
                            ("get", "/meetings/1/segments"), ("post", "/meetings/1/minutes/generate"),
                            ("get", "/meetings/1/minutes.pdf"), ("get", "/calendar/status")]:
            kwargs = {"json": {"meet_url": URL}} if method == "post" and url == "/meetings" else {}
            r = getattr(self.client, method)(url, **kwargs)
            self.assertEqual(r.status_code, 401, f"{method} {url}")
        with self.assertRaises(Exception):   # WebSocket ต้องปิดทิ้งถ้าไม่ได้ล็อกอิน
            with self.client.websocket_connect("/ws"):
                pass

    def test_setup_creates_scheduled_meeting_with_roles(self):
        mid = self.create_meeting()
        with self.owner():
            d = self.client.get(f"/meetings/{mid}").json()
        self.assertEqual(d["meeting"]["status"], "scheduled")
        self.assertEqual(d["header"]["chair"], "สมชาย ใจดี")
        self.assertEqual(d["header"]["secretary"], "สมหญิง")
        self.assertEqual([p["name"] for p in d["header"]["attendees"]], ["สมชาย ใจดี", "สมหญิง"])
        self.assertEqual({a["name"] for a in d["header"]["absent"]}, {"Alice", "Bob"})   # ยังไม่ยืนยัน = ไม่มา
        self.assertEqual(d["header"]["unconfirmed_count"], 2)

    def test_setup_validation(self):
        with self.owner():
            self.assertEqual(self.client.post("/meetings", json={"meet_url": "https://example.com/x"}).status_code, 400)
            two_chairs = {"meet_url": URL, "participants": [
                {"display_name": "A", "role": "chair"}, {"display_name": "B", "role": "chair"}]}
            self.assertEqual(self.client.post("/meetings", json=two_chairs).status_code, 400)
            bad_role = {"meet_url": URL, "participants": [{"display_name": "A", "role": "boss"}]}
            self.assertEqual(self.client.post("/meetings", json=bad_role).status_code, 400)
            before = len(self.client.get("/meetings").json())
            self.client.post("/meetings", json=bad_role)
            self.assertEqual(len(self.client.get("/meetings").json()), before)   # ไม่เหลือประชุมครึ่งๆ กลางๆ

    def test_other_users_cannot_see_or_touch_the_meeting(self):
        mid = self.create_meeting()
        self.record_transcript(mid)
        with self.as_user(self.other_id, "other@x.com"):
            self.assertEqual(self.client.get("/meetings").json(), [])
            for method, url, body in [
                ("get", f"/meetings/{mid}", None), ("get", f"/meetings/{mid}/segments", None),
                ("get", f"/meetings/{mid}/transcript.txt", None), ("post", f"/meetings/{mid}/verify-transcript", None),
                ("post", f"/meetings/{mid}/minutes/generate", None), ("get", f"/meetings/{mid}/minutes.pdf", None),
                ("post", f"/meetings/{mid}/participants", {"display_name": "x"}),
                ("patch", f"/meetings/{mid}", {"title": "แฮ็ก"}),
            ]:
                r = getattr(self.client, method)(url, **({"json": body} if body else {}))
                self.assertEqual(r.status_code, 404, f"{method} {url}")

    def test_chair_by_email_can_manage_but_plain_attendee_cannot(self):
        mid = self.create_meeting()
        with self.as_user(self.chair_id, "CHAIR@x.com"):       # อีเมลตรงกับประธาน (ไม่สนตัวพิมพ์)
            self.assertEqual(self.client.get(f"/meetings/{mid}").status_code, 200)
            self.assertIn(mid, [m["meeting_id"] for m in self.client.get("/meetings").json()])
        alice_id = db.upsert_user("sub-alice", "alice@x.com", "Alice", None)
        with self.as_user(alice_id, "alice@x.com"):            # เป็นแค่ผู้เข้าร่วม ไม่ใช่ประธาน/เลขา
            self.assertEqual(self.client.get(f"/meetings/{mid}").status_code, 404)

    def test_participant_editing_and_role_rules(self):
        mid = self.create_meeting()
        with self.owner():
            d = self.client.get(f"/meetings/{mid}").json()
            alice = next(p for p in d["participants"] if p["display_name"] == "Alice")
            r = self.client.patch(f"/participants/{alice['participant_id']}", json={"role": "chair"})
            self.assertEqual(r.status_code, 400)               # มีประธานแล้ว
            self.assertIn("มีประธานแล้ว", r.json()["detail"])
            r = self.client.patch(f"/participants/{alice['participant_id']}", json={"attendance": "present", "email": None})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["attendance"], "present")
            self.assertIsNone(r.json()["email"])
            self.assertEqual(self.client.delete(f"/participants/{alice['participant_id']}").status_code, 200)

    # ── ตรวจ transcript ──

    def test_transcript_review_gate(self):
        mid = self.create_meeting()
        self.record_transcript(mid)
        with self.owner():
            # ยังไม่ยืนยัน -> AI สร้างรายงานไม่ได้
            r = self.client.post(f"/meetings/{mid}/minutes/generate")
            self.assertEqual(r.status_code, 409)
            self.assertIn("ยืนยัน transcript", r.json()["detail"])

            segs = self.client.get(f"/meetings/{mid}/segments").json()
            self.assertEqual(len(segs), 3)
            self.assertEqual(self.client.patch(f"/segments/{segs[0]['segment_id']}", json={"text": "ผมเสนอให้อนุมัติงบสองหมื่นบาท ครับ"}).status_code, 200)
            self.assertEqual(self.client.patch(f"/segments/{segs[2]['segment_id']}", json={"deleted": True}).status_code, 200)
            self.assertEqual(self.client.patch(f"/segments/{segs[1]['segment_id']}", json={"text": "  "}).status_code, 400)
            segs = self.client.get(f"/meetings/{mid}/segments").json()
            self.assertEqual(segs[0]["original_text"], "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท")   # ต้นฉบับยังอยู่
            self.assertTrue(segs[2]["deleted"])
            txt = self.client.get(f"/meetings/{mid}/transcript.txt").text
            self.assertIn("ครับ", txt)
            self.assertNotIn("ทดสอบไมค์", txt)                  # ช่วงที่ลบแล้วไม่ถูกส่งต่อให้ AI/ไฟล์

            v = self.client.post(f"/meetings/{mid}/verify-transcript")
            self.assertEqual(v.status_code, 200)
            self.assertEqual(v.json()["unlinked_speakers"], [])    # Zed มีแต่ช่วงที่ลบ -> ไม่นับ
            # ยืนยันแล้ว -> แก้ transcript ไม่ได้อีก (ต้อง reopen)
            self.assertEqual(self.client.patch(f"/segments/{segs[0]['segment_id']}", json={"text": "แก้"}).status_code, 409)
            self.assertEqual(self.client.post(f"/meetings/{mid}/reopen-transcript").status_code, 200)
            self.assertEqual(self.client.patch(f"/segments/{segs[0]['segment_id']}", json={"text": "ผมเสนอให้อนุมัติงบสองหมื่นบาท ครับ"}).status_code, 200)

    def test_speaker_is_auto_linked_and_marks_attendance(self):
        mid = self.create_meeting()
        self.record_transcript(mid)
        with self.owner():
            d = self.client.get(f"/meetings/{mid}").json()
        by = {s["display_name"]: s for s in d["speakers"]}
        self.assertIsNotNone(by["สมชาย ใจดี (You)"]["participant_id"])       # (You) ถูกตัดตอนจับคู่
        self.assertIsNotNone(by["Alice"]["participant_id"])
        self.assertIsNone(by["Zed"]["participant_id"])
        alice = next(p for p in d["participants"] if p["display_name"] == "Alice")
        self.assertEqual(alice["attendance"], "present")                      # พูดแล้ว = เข้าร่วม

    # ── รายงาน: สร้าง / แก้ / อนุมัติ / PDF / Calendar ──

    def drive_to_draft(self):
        mid = self.create_meeting()
        self.record_transcript(mid)
        with self.owner():
            seg = self.client.get(f"/meetings/{mid}/segments").json()[2]
            self.client.patch(f"/segments/{seg['segment_id']}", json={"deleted": True})
            self.assertEqual(self.client.post(f"/meetings/{mid}/verify-transcript").status_code, 200)
        with mock.patch.object(summarizer, "_gemini_generate", lambda: fake_ai):
            with self.owner():
                r = self.client.post(f"/meetings/{mid}/minutes/generate")
        self.assertEqual(r.status_code, 200, r.text)
        return mid, r.json()

    def test_generate_flags_unreliable_ai_output(self):
        mid, out = self.drive_to_draft()
        codes = [w["code"] for w in out["warnings"]]
        self.assertIn("assignee_unknown", codes)          # "ใครสักคน" ไม่อยู่ในรายชื่อ
        self.assertIn("ungrounded", codes)                # หลักฐานที่ไม่มีใน transcript
        with self.owner():
            m = self.client.get(f"/meetings/{mid}/minutes").json()
        self.assertEqual(m["status"], "draft")
        self.assertTrue(m["editable"])
        self.assertEqual(m["minutes"]["version"], 1)
        self.assertIs(m["minutes"]["content"]["agenda"][0]["grounded"], True)

    def test_edit_then_approve_requires_confirmation_then_locks(self):
        mid, _ = self.drive_to_draft()
        with self.owner():
            m = self.client.get(f"/meetings/{mid}/minutes").json()
            content = m["minutes"]["content"]
            # คนแก้: ใส่ผู้รับผิดชอบที่ถูกต้อง + วันที่ และแก้ถ้อยคำมติ
            content["action_items"][1].update(assignee="Bob", due_date="2026-10-12")
            content["agenda"][0]["resolution"] = "ที่ประชุมอนุมัติงบประมาณ 20,000 บาท"
            r = self.client.put(f"/meetings/{mid}/minutes", json={"content": content})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("assignee_unknown", [w["code"] for w in r.json()["content"]["warnings"]])

            # ยังมีคำเตือน (หลักฐานไม่พบ / ยังมี invited) -> ต้องยืนยันก่อน
            r = self.client.post(f"/meetings/{mid}/minutes/approve", json={})
            self.assertEqual(r.status_code, 409)
            self.assertTrue(r.json()["detail"]["warnings"])
            self.assertEqual(self.client.get(f"/meetings/{mid}").json()["meeting"]["status"], "draft")

            r = self.client.post(f"/meetings/{mid}/minutes/approve", json={"confirm_warnings": True})
            self.assertEqual(r.status_code, 200, r.text)

            # อนุมัติแล้ว: ล็อก
            self.assertEqual(self.client.put(f"/meetings/{mid}/minutes", json={"content": content}).status_code, 409)
            self.assertEqual(self.client.post(f"/meetings/{mid}/minutes/generate").status_code, 409)
            self.assertEqual(self.client.patch(f"/meetings/{mid}", json={"title": "แก้"}).status_code, 409)
            self.assertEqual(self.client.post(f"/meetings/{mid}/participants", json={"display_name": "x"}).status_code, 409)
            seg = self.client.get(f"/meetings/{mid}/segments").json()[0]
            self.assertEqual(self.client.patch(f"/segments/{seg['segment_id']}", json={"text": "x"}).status_code, 409)

            m = self.client.get(f"/meetings/{mid}/minutes").json()
            self.assertEqual(m["status"], "approved")
            self.assertEqual(m["minutes"]["approved_by_name"], "เจ้าของ")
            self.assertEqual(len(m["action_items"]), 2)

    def test_pdf_has_draft_watermark_flag_until_approved(self):
        mid, _ = self.drive_to_draft()
        with self.owner():
            draft = self.client.get(f"/meetings/{mid}/minutes.pdf")
            self.assertEqual(draft.status_code, 200)
            self.assertTrue(draft.content.startswith(b"%PDF"))
            self.assertIn("draft", draft.headers["content-disposition"])
            self.client.post(f"/meetings/{mid}/minutes/approve", json={"confirm_warnings": True})
            final = self.client.get(f"/meetings/{mid}/minutes.pdf")
            self.assertNotIn("draft", final.headers["content-disposition"])
            self.assertNotEqual(draft.content, final.content)

    def test_revise_creates_new_version_and_keeps_approved_one(self):
        mid, _ = self.drive_to_draft()
        with self.owner():
            self.assertEqual(self.client.post(f"/meetings/{mid}/minutes/revise").status_code, 409)   # ยังไม่อนุมัติ
            self.client.post(f"/meetings/{mid}/minutes/approve", json={"confirm_warnings": True})
            r = self.client.post(f"/meetings/{mid}/minutes/revise")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["version"], 2)
            m = self.client.get(f"/meetings/{mid}/minutes").json()
            self.assertEqual((m["status"], m["minutes"]["version"], m["minutes"]["status"]), ("draft", 2, "draft"))
        rows = self.rows("SELECT version, status FROM minutes WHERE meeting_id=%s ORDER BY version", (mid,))
        self.assertEqual([(r["version"], r["status"]) for r in rows], [(1, "approved"), (2, "draft")])   # ฉบับอนุมัติยังอยู่

    def test_calendar_sync_only_after_approval_and_per_user(self):
        mid, _ = self.drive_to_draft()
        fake = FakeCalendar()
        with self.owner():
            # แก้ให้ action item ที่สองมีวันที่ แล้วอนุมัติ
            content = self.client.get(f"/meetings/{mid}/minutes").json()["minutes"]["content"]
            content["action_items"][1].update(assignee="Bob", due_date="2026-10-12")
            self.client.put(f"/meetings/{mid}/minutes", json={"content": content})
            self.assertEqual(self.client.post(f"/meetings/{mid}/calendar/sync-all").status_code, 409)   # ยังไม่อนุมัติ
            self.client.post(f"/meetings/{mid}/minutes/approve", json={"confirm_warnings": True})

            with mock.patch.object(calendar_sync_mod(), "build", lambda *a, **k: fake), \
                 mock.patch.object(calendar_sync_mod().calendar_auth, "get_credentials", lambda uid: object()):
                r = self.client.post(f"/meetings/{mid}/calendar/sync-all")
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(all(x["ok"] for x in r.json()["results"]), r.json())
                self.assertEqual(len(fake.inserted), 2)
                alice_ev = next(b for b, _ in fake.inserted if b["summary"] == "ส่งรายงานความก้าวหน้า")
                self.assertEqual(alice_ev["attendees"], [{"email": "alice@x.com"}])
                self.assertEqual(alice_ev["start"]["dateTime"], "2026-10-09T13:00:00")
                self.assertEqual({s for _, s in fake.inserted}, {"none"})      # ค่าเริ่มต้นไม่ส่งอีเมลเชิญจริง
                # ส่งซ้ำไม่สร้าง event ซ้ำ
                again = self.client.post(f"/meetings/{mid}/calendar/sync-all").json()
                self.assertTrue(all(x.get("already") for x in again["results"]))
                self.assertEqual(len(fake.inserted), 2)

        with self.as_user(self.other_id, "other@x.com"):
            item = db.list_minutes_action_items(db.get_latest_minutes(mid)["minutes_id"])[0]
            self.assertEqual(self.client.post(f"/action-items/{item['action_item_id']}/sync-to-calendar").status_code, 404)

    def test_calendar_item_without_date_is_reported_not_guessed(self):
        mid, _ = self.drive_to_draft()          # action item ที่สองไม่มีวันที่
        fake = FakeCalendar()
        with self.owner():
            self.client.post(f"/meetings/{mid}/minutes/approve", json={"confirm_warnings": True})
            with mock.patch.object(calendar_sync_mod(), "build", lambda *a, **k: fake), \
                 mock.patch.object(calendar_sync_mod().calendar_auth, "get_credentials", lambda uid: object()):
                r = self.client.post(f"/meetings/{mid}/calendar/sync-all").json()
        oks = [x["ok"] for x in r["results"]]
        self.assertEqual(sorted(oks), [False, True])
        self.assertEqual(len(fake.inserted), 1)
        self.assertIn("ยังไม่มีกำหนดวัน", next(x for x in r["results"] if not x["ok"])["error"])

    def test_calendar_tokens_are_per_user(self):
        db.save_calendar_token(self.owner_id, '{"a": 1}')
        self.assertEqual(db.get_calendar_token(self.owner_id), '{"a": 1}')
        self.assertIsNone(db.get_calendar_token(self.other_id))
        with self.as_user(self.other_id, "other@x.com"):
            self.assertFalse(self.client.get("/calendar/status").json()["connected"])
        with self.owner():
            self.assertTrue(self.client.get("/calendar/status").json()["connected"])

    # ── หน้าเว็บ ──

    def test_pages_are_served_only_when_logged_in(self):
        self.assertEqual(self.client.get("/meeting/new", follow_redirects=False).status_code, 307)
        with self.owner():
            r = self.client.get("/meeting/new")
            self.assertEqual(r.status_code, 200)
            self.assertIn("MeetSync", r.text)
            self.assertEqual(self.client.get("/").status_code, 200)


def calendar_sync_mod():
    import calendar_sync
    return calendar_sync


if __name__ == "__main__":
    unittest.main()
