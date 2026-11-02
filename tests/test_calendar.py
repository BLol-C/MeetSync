"""ทดสอบการสร้าง Calendar event (ฟังก์ชันล้วน ไม่ต่อ Google) รวมบั๊กที่เคยพบ: ข้ามเที่ยงคืน, event ทั้งวัน, ไม่มีวัน"""

import datetime
import os
import sys
import unittest

from tests.dbcase import ROOT  # noqa: F401  (ทำให้ sys.path ถูกต้อง + โหลด .env)

os.environ.setdefault("SESSION_SECRET", "test")

from integrations import calendar_sync  # noqa: E402

ITEM = {"description": "ส่งรายงาน", "assignee": "Alice", "due_date": "2026-10-09",
        "due_time": None, "due_time_end": None}


class ConnectLinkTests(unittest.TestCase):
    """ล็อกอินแล้วเชื่อม Calendar ต่อ: Google ต้องเลือกบัญชีที่ล็อกอินอยู่ให้ (login_hint)"""

    def setUp(self):
        os.environ.setdefault("GOOGLE_CLIENT_ID", "test-id")
        os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-secret")

    def test_auth_url_carries_login_hint(self):
        from integrations import calendar_auth
        url = calendar_auth.build_auth_url("state123", login_hint="me@example.com")
        self.assertIn("login_hint=me%40example.com", url)
        self.assertIn("access_type=offline", url)

    def test_auth_url_without_hint_has_none(self):
        from integrations import calendar_auth
        self.assertNotIn("login_hint", calendar_auth.build_auth_url("state123"))

    def test_token_round_trip_keeps_email(self):
        from bot import botclient
        data = botclient.read_calendar_token(botclient.calendar_connect_token(7, "http://x/", "me@example.com"), 60)
        self.assertEqual(data, {"uid": 7, "ret": "http://x/", "em": "me@example.com"})

    def test_connect_url_uses_user_email(self):
        from bot import botclient
        url = botclient.calendar_connect_url({"user_id": 7, "email": "me@example.com"}, "http://x/")
        token = url.split("t=", 1)[1]
        import urllib.parse
        self.assertEqual(botclient.read_calendar_token(urllib.parse.unquote(token), 60)["em"], "me@example.com")


class EventScheduleTests(unittest.TestCase):
    def test_all_day_end_is_exclusive_in_google_and_inclusive_in_meetsync(self):
        one = calendar_sync.event_schedule({"start": {"date": "2026-11-02"}, "end": {"date": "2026-11-03"}})
        self.assertEqual(one, {"due_date": "2026-11-02", "due_date_end": None, "due_time": None, "due_time_end": None})
        many = calendar_sync.event_schedule({"start": {"date": "2026-11-02"}, "end": {"date": "2026-11-05"}})
        self.assertEqual((many["due_date"], many["due_date_end"]), ("2026-11-02", "2026-11-04"))

    def test_timed_event_is_converted_to_thai_time(self):
        s = calendar_sync.event_schedule({"start": {"dateTime": "2026-11-02T05:00:00Z"}, "end": {"dateTime": "2026-11-02T05:50:00Z"}})
        self.assertEqual((s["due_time"], s["due_time_end"], s["due_date"]), ("12:00", "12:50", "2026-11-02"))
        late = calendar_sync.event_schedule({"start": {"dateTime": "2026-11-02T20:00:00Z"}, "end": {"dateTime": "2026-11-02T21:00:00Z"}})
        self.assertEqual((late["due_date"], late["due_time"]), ("2026-11-03", "03:00"))      # ข้ามวันเมื่อแปลงเป็นเวลาไทย

    def test_multi_day_timed_event_has_an_end_date(self):
        s = calendar_sync.event_schedule({"start": {"dateTime": "2026-11-27T12:00:00+07:00"}, "end": {"dateTime": "2026-11-28T12:50:00+07:00"}})
        self.assertEqual((s["due_date"], s["due_date_end"], s["due_time"], s["due_time_end"]), ("2026-11-27", "2026-11-28", "12:00", "12:50"))

    def test_what_meetsync_sent_reads_back_as_unchanged_even_for_start_only_and_overnight_items(self):
        for extra in ({}, {"due_time": "13:00"}, {"due_time": "23:30"}, {"due_time": "23:30", "due_time_end": "00:30"},
                      {"due_time": "10:00", "due_time_end": "12:00", "due_date_end": "2026-10-11"}, {"due_date_end": "2026-10-11"}):
            item = {**ITEM, **extra}
            event = calendar_sync.build_event_body(item)
            self.assertEqual(calendar_sync.compare_with_google(item, event)["status"], "same", extra)

    def test_renaming_the_event_is_a_change_but_blank_or_respaced_titles_are_not(self):
        item = {**ITEM, "description": "ส่งรายงาน"}
        event = calendar_sync.build_event_body(item)
        self.assertEqual(calendar_sync.compare_with_google(item, {**event, "summary": "  ส่งรายงาน  "})["status"], "same")    # ช่องว่างไม่นับ
        self.assertEqual(calendar_sync.compare_with_google(item, {**event, "summary": ""})["status"], "same")                  # ชื่อว่าง = คงชื่อเดิม
        r = calendar_sync.compare_with_google(item, {**event, "summary": "[ด่วน] ส่งรายงานฉบับแก้"})
        self.assertEqual((r["status"], r["mine"]["description"], r["remote"]["description"]), ("changed", "ส่งรายงาน", "[ด่วน] ส่งรายงานฉบับแก้"))
        gone = calendar_sync.compare_with_google(item, {**event, "summary": ""})
        self.assertEqual(gone["remote"]["description"], "ส่งรายงาน")                                                          # ชื่อว่างไม่ถูกเอาไปเขียนทับ

    def test_changed_and_deleted_are_told_apart(self):
        item = {**ITEM, "due_time": "13:00", "due_time_end": "14:00"}
        moved = calendar_sync.build_event_body({**item, "due_date": "2026-10-10"})
        r = calendar_sync.compare_with_google(item, moved)
        self.assertEqual((r["status"], r["remote"]["due_date"], r["mine"]["due_date"]), ("changed", "2026-10-10", "2026-10-09"))
        self.assertEqual(calendar_sync.compare_with_google(item, None)["status"], "deleted")


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

    def test_multi_day_all_day_event_ends_the_day_after_the_last_day(self):
        b = calendar_sync.build_event_body({**ITEM, "due_date_end": "2026-10-11"})      # 9-11 ต.ค. ไม่ระบุเวลา = เต็มวันต่อเนื่อง
        self.assertEqual(b["start"], {"date": "2026-10-09"})
        self.assertEqual(b["end"], {"date": "2026-10-12"})                              # Google นับ end.date แบบไม่รวมวันนั้น
        self.assertIn("2026-10-09 ถึง 2026-10-11", b["description"])

    def test_multi_day_timed_event_runs_from_start_time_to_end_time_on_the_last_day(self):
        b = calendar_sync.build_event_body({**ITEM, "due_date_end": "2026-10-11", "due_time": "10:00", "due_time_end": "12:00"})
        self.assertEqual(b["start"]["dateTime"], "2026-10-09T10:00:00")
        self.assertEqual(b["end"]["dateTime"], "2026-10-11T12:00:00")

    def test_multi_day_with_start_time_only_ends_one_hour_after_that_time_on_the_last_day(self):
        b = calendar_sync.build_event_body({**ITEM, "due_date_end": "2026-10-11", "due_time": "10:00"})
        self.assertEqual(b["end"]["dateTime"], "2026-10-11T11:00:00")

    def test_end_date_same_as_start_or_empty_behaves_like_a_single_day(self):
        for end in (None, "", "2026-10-09"):
            b = calendar_sync.build_event_body({**ITEM, "due_date_end": end})
            self.assertEqual((b["start"], b["end"]), ({"date": "2026-10-09"}, {"date": "2026-10-10"}))
        b = calendar_sync.build_event_body({**ITEM, "due_date_end": "2026-10-09", "due_time": "23:30", "due_time_end": "00:30"})
        self.assertEqual(b["end"]["dateTime"], "2026-10-10T00:30:00")                    # กฎข้ามเที่ยงคืนเดิมยังใช้

    def test_end_date_before_start_is_refused(self):
        with self.assertRaisesRegex(ValueError, "อยู่ก่อนวันที่เริ่ม"):
            calendar_sync.build_event_body({**ITEM, "due_date_end": "2026-10-08"})

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
