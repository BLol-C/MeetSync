"""ทดสอบการสร้าง Calendar event (ฟังก์ชันล้วน ไม่ต่อ Google) รวมบั๊กที่เคยพบ: ข้ามเที่ยงคืน, event ทั้งวัน, ไม่มีวัน"""

import datetime
import os
import sys
import unittest

from tests.dbcase import ROOT  # noqa: F401  (ทำให้ sys.path ถูกต้อง + โหลด .env)

os.environ.setdefault("SESSION_SECRET", "test")

import calendar_sync  # noqa: E402

ITEM = {"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-09",
        "due_time": None, "due_time_end": None}


class ConnectLinkTests(unittest.TestCase):
    """ล็อกอินแล้วเชื่อม Calendar ต่อ: Google ต้องเลือกบัญชีที่ล็อกอินอยู่ให้ (login_hint)"""

    def setUp(self):
        os.environ.setdefault("GOOGLE_CLIENT_ID", "test-id")
        os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-secret")

    def test_auth_url_carries_login_hint(self):
        import calendar_auth
        url = calendar_auth.build_auth_url("state123", login_hint="me@example.com")
        self.assertIn("login_hint=me%40example.com", url)
        self.assertIn("access_type=offline", url)

    def test_auth_url_without_hint_has_none(self):
        import calendar_auth
        self.assertNotIn("login_hint", calendar_auth.build_auth_url("state123"))

    def test_token_round_trip_keeps_email(self):
        import botclient
        data = botclient.read_calendar_token(botclient.calendar_connect_token(7, "http://x/", "me@example.com"), 60)
        self.assertEqual(data, {"uid": 7, "ret": "http://x/", "em": "me@example.com"})

    def test_connect_url_uses_user_email(self):
        import botclient
        url = botclient.calendar_connect_url({"user_id": 7, "email": "me@example.com"}, "http://x/")
        token = url.split("t=", 1)[1]
        import urllib.parse
        self.assertEqual(botclient.read_calendar_token(urllib.parse.unquote(token), 60)["em"], "me@example.com")


class BuildEventTests(unittest.TestCase):
    def test_all_day_event_end_is_exclusive_next_day(self):
        b = calendar_sync.build_event_body(ITEM)
        self.assertEqual(b["start"], {"date": "2026-10-09"})
        self.assertEqual(b["end"], {"date": "2026-10-10"})   # Google นับ end.date แบบไม่รวมวันนั้น
        self.assertNotIn("attendees", b)

    def test_timed_event_defaults_to_one_hour(self):
        b = calendar_sync.build_event_body({**ITEM, "due_time": "13:00"})
        self.assertEqual(b["start"]["dateTime"], "2026-10-09T13:00:00")
        self.assertEqual(b["end"]["dateTime"], "2026-10-09T14:00:00")
        self.assertEqual(b["start"]["timeZone"], "Asia/Bangkok")

    def test_explicit_end_time(self):
        b = calendar_sync.build_event_body({**ITEM, "due_time": "13:00", "due_time_end": "16:00"})
        self.assertEqual(b["end"]["dateTime"], "2026-10-09T16:00:00")

    def test_late_start_rolls_over_midnight_instead_of_ending_before_it_starts(self):
        b = calendar_sync.build_event_body({**ITEM, "due_time": "23:30"})
        self.assertEqual(b["end"]["dateTime"], "2026-10-10T00:30:00")   # เดิมได้ 2026-10-09T00:30 (จบก่อนเริ่ม)
        b = calendar_sync.build_event_body({**ITEM, "due_time": "23:30", "due_time_end": "00:30"})
        self.assertEqual(b["end"]["dateTime"], "2026-10-10T00:30:00")

    def test_missing_date_is_refused_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "ยังไม่มีกำหนดวัน"):
            calendar_sync.build_event_body({**ITEM, "due_date": None})

    def test_accepts_date_objects_and_adds_attendee(self):
        b = calendar_sync.build_event_body({**ITEM, "due_date": datetime.date(2026, 10, 9)}, "alice@x.com")
        self.assertEqual(b["attendees"], [{"email": "alice@x.com"}])
        self.assertIn("ผู้รับผิดชอบ: Alice", b["description"])


class AttendeeEmailTests(unittest.TestCase):
    PARTS = [
        {"display_name": "Alice", "email": "alice@x.com"},
        {"display_name": "Bob", "email": None},
        {"display_name": "Dan A", "email": "a@x.com"},
        {"display_name": "Dan B", "email": "b@x.com"},
    ]

    def test_matches_by_name(self):
        self.assertEqual(calendar_sync.attendee_email(self.PARTS, "alice"), "alice@x.com")
        self.assertEqual(calendar_sync.attendee_email(self.PARTS, "คุณ Alice"), "alice@x.com")

    def test_no_email_or_unknown_or_ambiguous_gives_none(self):
        self.assertIsNone(calendar_sync.attendee_email(self.PARTS, "Bob"))     # ไม่มีอีเมล
        self.assertIsNone(calendar_sync.attendee_email(self.PARTS, "Zed"))     # ไม่อยู่ในรายชื่อ
        self.assertIsNone(calendar_sync.attendee_email(self.PARTS, "Dan"))     # ตรงสองคน ไม่เดา
        self.assertIsNone(calendar_sync.attendee_email(self.PARTS, None))


if __name__ == "__main__":
    unittest.main()
