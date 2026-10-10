"""
ทดสอบชั้นบริการ (service.py) แบบครบวงจรกับ MySQL ชั่วคราว: สิทธิ์ -> ตั้งค่าประชุม -> ผู้เข้าร่วม -> ตรวจ transcript ->
AI ร่าง (ใช้ AI ปลอม) -> แก้ -> อนุมัติ -> PDF -> Calendar (ใช้ Calendar ปลอม) ไม่เรียก Gemini/Google จริง
"""

import datetime
import json
import unittest
import uuid
from unittest import mock

from tests.dbcase import TempDbCase

from integrations import calendar_sync  # noqa: E402
import db  # noqa: E402
import service  # noqa: E402
from reports import summarizer  # noqa: E402
from service import ServiceError  # noqa: E402

URL = "https://meet.google.com/abc-defg-hij"


def fake_ai(prompt, schema=None):
    return json.dumps({
        "summary": "ที่ประชุมอนุมัติงบประมาณ",
        "agenda": [{
            "section": "consider_new", "title": "งบประมาณ", "discussion": "นายสมชายเสนอให้อนุมัติงบ", "resolution": "อนุมัติงบสองหมื่นบาท",
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


class FakeHttpError(Exception):
    """แทน googleapiclient.errors.HttpError (ใช้แค่ resp.status)"""

    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.resp = type("Resp", (), {"status": status})()


class FakeCalendar:
    def __init__(self):
        self.inserted = []      # (body, sendUpdates)
        self.deleted = []       # eventId
        self.delete_error = None  # ตั้งเป็นสถานะ HTTP เพื่อจำลอง Google ตอบ error ตอนลบ
        self._last = None

    def events(self):
        return self

    def insert(self, calendarId, body, sendUpdates):  # noqa: N803
        self.inserted.append((body, sendUpdates))
        self._last = ("insert", f"evt{len(self.inserted)}")
        return self

    def delete(self, calendarId, eventId, sendUpdates):  # noqa: N803
        self.deleted.append(eventId)
        self._last = ("delete", eventId)
        return self

    def execute(self):
        kind, event_id = self._last
        if kind == "delete":
            if self.delete_error:
                raise FakeHttpError(self.delete_error)
            return {}
        return {"id": event_id, "htmlLink": f"https://calendar.google.com/event?eid={event_id}"}


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

    def test_header_follows_the_form_guests_and_absence_reasons(self):
        # แบบฟอร์ม: ผู้มาประชุม (กรรมการ) / ผู้เข้าร่วมประชุม (ไม่ใช่กรรมการ) / ผู้ไม่มาประชุม + สาเหตุในวงเล็บ
        mid = self.new_meeting(people=[
            {"display_name": "ต้น", "role": "chair", "attendance": "present"},
            {"display_name": "ฟ้า", "role": "secretary", "attendance": "present"},
            {"display_name": "แพร", "attendance": "absent", "absence_reason": "ติดภารกิจ"},
            {"display_name": "คุณสมชาย", "role": "guest", "attendance": "present"},
            {"display_name": "คุณนิดา", "role": "guest", "attendance": "absent", "absence_reason": "  ลาป่วย  "},
            {"display_name": "บอล", "attendance": "absent"},
        ])
        h = service.get_detail(self.owner, mid)["header"]
        self.assertEqual([a["name"] for a in h["attendees"]], ["ต้น", "ฟ้า"])            # กรรมการเท่านั้น ไม่ปนผู้เข้าร่วม
        self.assertEqual([a["name"] for a in h["guests"]], ["คุณสมชาย"])
        reasons = {a["name"]: a["reason"] for a in h["absent"]}
        self.assertEqual(reasons, {"แพร": "ติดภารกิจ", "คุณนิดา": "ลาป่วย", "บอล": None})   # ตัดช่องว่าง, ไม่ใส่สาเหตุ = None

    def test_absence_reason_and_guest_role_are_editable_in_the_people_table(self):
        mid = self.new_meeting()
        people = {p["display_name"]: p for p in service.get_detail(self.owner, mid)["people"]}
        rows = [{"speaker_id": p["speaker_id"], "display_name": p["display_name"], "email": p["email"] or "",
                 "role": p["role"], "attendance": p["attendance"], "absence_reason": p.get("absence_reason") or ""}
                for p in people.values()]
        bob = next(r for r in rows if r["display_name"] == "Bob")
        bob.update(attendance="absent", absence_reason="ลาพักร้อน")
        alice = next(r for r in rows if r["display_name"] == "Alice")
        alice.update(role="guest", attendance="present")
        rows.append({"speaker_id": None, "display_name": "คุณใหม่", "role": "guest", "attendance": "absent",
                     "absence_reason": "ติดธุระ"})
        result = service.apply_people_table(self.owner, mid, rows)
        self.assertEqual(result["errors"], [])
        h = service.get_detail(self.owner, mid)["header"]
        self.assertEqual({a["name"]: a["reason"] for a in h["absent"]}, {"Bob": "ลาพักร้อน", "คุณใหม่": "ติดธุระ"})
        self.assertEqual([a["name"] for a in h["guests"]], ["Alice"])

    # ── วาระ 5 หมวด + เติมจากการประชุมครั้งก่อน ──

    def approved_meeting(self):
        mid = self.draft()
        service.approve_report(self.owner, mid, confirm_warnings=True)
        return mid

    def test_agenda_items_keep_their_section_and_only_ai_sections_need_evidence(self):
        mid = self.draft()
        content = service.get_report_view(self.owner, mid)["report"]["content"]
        agenda = list(content["agenda"])
        agenda += [
            {"section": "followup", "title": "ติดตามงานเดิม", "discussion": "", "resolution": "อยู่ระหว่างดำเนินการ", "evidence": []},
            {"section": "inform", "title": "แจ้งงานเปิดบ้าน", "discussion": "วันศุกร์หน้า", "resolution": None, "evidence": []},
            {"section": "approve_prev", "title": "รับรองรายงานการประชุมครั้งที่ 1/2569", "discussion": "",
             "resolution": "ที่ประชุมรับรอง", "evidence": []},
            {"section": "bogus", "title": "ค่าผิดต้องกลับเป็นค่าเริ่มต้น", "discussion": "x", "resolution": None, "evidence": []},
        ]
        cleaned = service.save_report_draft(self.owner, mid, {**content, "agenda": agenda})
        stored = db.get_report(mid)["content"]["agenda"]
        order = [db.AGENDA_SECTIONS.index(a["section"]) for a in stored]
        self.assertEqual(order, sorted(order))                                   # เก็บเรียงตามหมวดวาระ
        by_title = {a["title"]: a["section"] for a in stored}
        self.assertEqual(by_title["ค่าผิดต้องกลับเป็นค่าเริ่มต้น"], "consider_new")
        self.assertEqual(by_title["แจ้งงานเปิดบ้าน"], "inform")
        for i, a in enumerate(cleaned["agenda"]):                                # มติที่คนกรอกเองในหมวดที่ไม่ใช่ของ AI ไม่ถูกเตือนเรื่องหลักฐาน
            if a["section"] in ("followup", "approve_prev"):
                self.assertFalse([w for w in cleaned["warnings"] if w["path"].startswith(f"agenda[{i}]")], a["title"])

    def test_previous_meeting_must_be_an_approved_meeting_you_manage(self):
        prev = self.approved_meeting()
        unapproved = self.new_meeting()
        cur = self.new_meeting(previous_meeting_id=prev)
        self.assertEqual(service.get_detail(self.owner, cur)["meeting"]["previous_meeting_id"], prev)
        offered = [m["meeting_id"] for m in service.previous_meeting_choices(self.owner)]
        self.assertIn(prev, offered)
        self.assertNotIn(unapproved, offered)                                                # เสนอเฉพาะที่อนุมัติรายงานแล้ว
        self.assertNotIn(cur, [m["meeting_id"] for m in service.previous_meeting_choices(self.owner, cur)])   # ไม่เสนอตัวเอง
        self.assertEqual(service.previous_meeting_choices(self.other), [])                   # ไม่เห็นการประชุมของคนอื่น
        for bad in (unapproved, cur, 999999, "abc"):                                          # ยังไม่อนุมัติ / ตัวเอง / ไม่มี / ไม่ใช่เลข
            with self.assertRaises(ServiceError):
                service.update_meeting(self.owner, cur, previous_meeting_id=bad)
        service.update_meeting(self.owner, cur, previous_meeting_id=None)
        self.assertIsNone(service.get_detail(self.owner, cur)["meeting"]["previous_meeting_id"])
        with self.assertRaises(ServiceError):
            self.new_meeting(previous_meeting_id=unapproved)

    def generate_for(self, **over):
        mid = self.new_meeting(**over)
        self.record(mid)
        service.verify_transcript(self.owner, mid)
        service.generate_report(self.owner, mid, generate=fake_ai)
        return mid

    def test_generating_a_report_prefills_sections_2_3_and_4_1_from_the_previous_meeting(self):
        prev = self.approved_meeting()
        prev_report = db.get_report(prev)["content"]
        prev_meeting = db.get_meeting(prev)
        cur = self.generate_for(previous_meeting_id=prev, meeting_no="4/2569")
        report = db.get_report(cur)
        by = {}
        for a in report["content"]["agenda"]:
            by.setdefault(a["section"], []).append(a)
        approve = by["approve_prev"][0]["title"]
        self.assertIn("ครั้งที่ 3/2569", approve)                                          # มาจาก meeting_no ของครั้งก่อน
        self.assertIn(str(prev_meeting["started_at"].year + 543), approve)
        self.assertEqual([a["title"] for a in by["followup"]], [x["description"] for x in prev_report["action_items"]])
        self.assertIn("ผู้รับผิดชอบ: Alice", by["followup"][0]["discussion"])
        self.assertNotIn("consider_old", by)                                               # ครั้งก่อนทุกวาระมีมติ จึงไม่มีเรื่องค้าง
        self.assertEqual([a["title"] for a in by["consider_new"]], ["งบประมาณ"])           # ที่ AI สรุปยังอยู่ 4.2
        self.assertEqual({a.get("section") for a in report["ai_snapshot"]["agenda"]}, {"consider_new"})   # snapshot เก็บเฉพาะที่ AI ร่าง
        alone = self.generate_for()                                                        # ไม่เลือกครั้งก่อน = ไม่มีหมวดที่เติมให้
        self.assertEqual({a["section"] for a in db.get_report(alone)["content"]["agenda"]}, {"consider_new"})

    def test_unresolved_agenda_items_of_the_previous_meeting_become_section_4_1(self):
        prev = self.draft()
        content = service.get_report_view(self.owner, prev)["report"]["content"]
        content["agenda"].append({"section": "consider_new", "title": "เรื่องที่ยังไม่ตัดสินใจ", "discussion": "รอข้อมูลเพิ่ม",
                                  "resolution": None, "evidence": ["ผมเสนอให้อนุมัติงบสองหมื่นบาท"]})
        content["agenda"].append({"section": "inform", "title": "เรื่องแจ้งทราบ", "discussion": "x", "resolution": None, "evidence": []})
        service.save_report_draft(self.owner, prev, content)
        service.approve_report(self.owner, prev, confirm_warnings=True)
        cur = self.generate_for(previous_meeting_id=prev)
        old = [a for a in db.get_report(cur)["content"]["agenda"] if a["section"] == "consider_old"]
        self.assertEqual([(a["title"], a["discussion"], a["resolution"]) for a in old],
                         [("เรื่องที่ยังไม่ตัดสินใจ", "รอข้อมูลเพิ่ม", None)])         # เรื่องแจ้งทราบ (inform) ไม่ถูกนับเป็นเรื่องค้าง

    def test_pdf_places_each_item_under_its_own_agenda_heading(self):
        import io

        from pypdf import PdfReader

        from reports import pdf_report
        mid = self.new_meeting()
        content = {"summary": "", "other_matters": "เรื่องอื่นทดสอบ", "action_items": [], "agenda": [
            {"section": "inform", "title": "ก_แจ้งทราบ", "discussion": "รายละเอียดแจ้ง", "resolution": None, "evidence": []},
            {"section": "approve_prev", "title": "รับรองรายงานการประชุมครั้งที่ 3/2569 เมื่อวันที่ 2 ตุลาคม 2569",
             "discussion": "", "resolution": "ที่ประชุมรับรองรายงานทดสอบ", "evidence": []},
            {"section": "followup", "title": "ข_สืบเนื่อง", "discussion": "ผู้รับผิดชอบ: Alice", "resolution": None, "evidence": []},
            {"section": "consider_old", "title": "ค_ค้างพิจารณา", "discussion": "", "resolution": "เลื่อนไปครั้งหน้า", "evidence": []},
            {"section": "consider_new", "title": "ง_พิจารณาใหม่", "discussion": "", "resolution": "อนุมัติ", "evidence": []},
        ]}
        data = pdf_report.build_minutes_pdf(db.get_meeting(mid), db.list_speakers(mid), content)
        text = chr(10).join(pg.extract_text() for pg in PdfReader(io.BytesIO(data)).pages)
        order = ["ระเบียบวาระที่ 1", "ก_แจ้งทราบ", "รายละเอียดแจ้ง", "ระเบียบวาระที่ 2", "รับรองรายงานการประชุมครั้งที่ 3/2569",
                 "ที่ประชุมรับรองรายงานทดสอบ", "ระเบียบวาระที่ 3", "ข_สืบเนื่อง", "ระเบียบวาระที่ 4", "4.1", "ค_ค้างพิจารณา",
                 "เลื่อนไปครั้งหน้า", "4.2", "ง_พิจารณาใหม่", "ระเบียบวาระที่ 5", "เรื่องอื่นทดสอบ"]
        pos = -1
        for marker in order:
            nxt = text.find(marker, pos + 1)
            self.assertGreater(nxt, pos, f"ไม่พบ/ลำดับผิด: {marker!r}")
            pos = nxt
        self.assertIn("1.1 ก_แจ้งทราบ", text)
        self.assertIn("4.1.1", text)
        self.assertIn("4.2.1", text)

    def test_position_is_saved_trimmed_and_shown_in_the_pdf_when_present(self):
        import io

        from pypdf import PdfReader

        from reports import pdf_report
        mid = self.new_meeting(people=[
            {"display_name": "ต้น", "role": "chair", "attendance": "present", "position": "  หัวหน้าสาขาวิชา  "},
            {"display_name": "ฟ้า", "role": "secretary", "attendance": "present"},
            {"display_name": "มด", "attendance": "present", "position": ""},
            {"display_name": "คุณแขก", "role": "guest", "attendance": "present", "position": "ผู้ประสานงานโครงการ"},
            {"display_name": "คุณแขกสอง", "role": "guest", "attendance": "present"},
        ])
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        self.assertEqual(people["ต้น"]["position"], "หัวหน้าสาขาวิชา")      # ตัดช่องว่าง
        self.assertIsNone(people["ฟ้า"]["position"])
        self.assertIsNone(people["มด"]["position"])                          # ว่าง = None
        # แก้ตำแหน่งผ่านตารางผู้เข้าร่วมในหน้าเว็บ
        rows = [{"speaker_id": p["speaker_id"], "display_name": p["display_name"], "email": p["email"] or "",
                 "role": p["role"], "attendance": p["attendance"], "absence_reason": "",
                 "position": p["position"] or ""} for p in people.values()]
        next(r for r in rows if r["display_name"] == "มด")["position"] = "อาจารย์"
        self.assertEqual(service.apply_people_table(self.owner, mid, rows)["errors"], [])
        self.assertEqual({p["display_name"]: p["position"] for p in db.list_speakers(mid)},
                         {"ต้น": "หัวหน้าสาขาวิชา", "ฟ้า": None, "มด": "อาจารย์",
                          "คุณแขก": "ผู้ประสานงานโครงการ", "คุณแขกสอง": None})
        header = service.get_detail(self.owner, mid)["header"]
        self.assertEqual({a["name"]: a["position"] for a in header["attendees"]},
                         {"ต้น": "หัวหน้าสาขาวิชา", "ฟ้า": None, "มด": "อาจารย์"})
        self.assertEqual({a["name"]: a["position"] for a in header["guests"]},
                         {"คุณแขก": "ผู้ประสานงานโครงการ", "คุณแขกสอง": None})
        data = pdf_report.build_minutes_pdf(db.get_meeting(mid), db.list_speakers(mid), {"agenda": [], "action_items": []})
        text = chr(10).join(pg.extract_text() for pg in PdfReader(io.BytesIO(data)).pages)
        self.assertIn("หัวหน้าสาขาวิชา", text)           # กรอกตำแหน่งไว้ = แสดงตำแหน่งนั้น
        self.assertIn("อาจารย์", text)
        self.assertIn("เลขานุการ", text)                  # ไม่ได้กรอก = ใช้บทบาทในที่ประชุมเหมือนเดิม
        self.assertIn("ผู้ประสานงานโครงการ", text)         # ผู้เข้าร่วมที่ไม่ใช่กรรมการก็แสดงถ้ากรอกไว้

    def test_set_person_attendance_works_while_editable_and_not_after_approval(self):
        mid = self.new_meeting()
        bob = next(p for p in db.list_speakers(mid) if p["display_name"] == "Bob")
        service.set_person_attendance(self.owner, bob["speaker_id"], "present")
        self.assertEqual(db.get_speaker(bob["speaker_id"])["attendance"], "present")
        with self.assertRaises(ServiceError):
            service.set_person_attendance(self.owner, bob["speaker_id"], "maybe")
        with self.assertRaises(ServiceError):
            service.set_person_attendance(self.other, bob["speaker_id"], "absent")        # คนนอกสิทธิ์
        done = self.approved_meeting()
        someone = db.list_speakers(done)[0]
        with self.assertRaises(ServiceError):
            service.set_person_attendance(self.owner, someone["speaker_id"], "absent")    # อนุมัติแล้วแก้ไม่ได้

    # ── ชื่อภาษาอังกฤษ / ชื่อที่ Meet แสดง (meet_alias) ──

    def test_meet_name_given_at_registration_matches_speakers_and_the_report_keeps_the_thai_name(self):
        mid = self.new_meeting(people=[
            {"display_name": "ธนาวีร์ บุญเกิด", "meet_alias": "  Thanawee BOONKERD ", "email": "thanawee.b@ku.th",
             "role": "chair", "attendance": "present"},
            {"display_name": "ภาม", "role": "secretary", "attendance": "present"},
            {"display_name": "ศศิน", "meet_alias": "Sasin TIENDEE"},
        ])
        people = {p["display_name"]: p for p in db.list_speakers(mid)}
        self.assertEqual(people["ธนาวีร์ บุญเกิด"]["meet_alias"], "Thanawee BOONKERD")       # ตัดช่องว่าง
        self.assertIsNone(people["ภาม"]["meet_alias"])
        speaker = db.get_or_create_speaker(mid, "Sasin TIENDEE (You)")                    # ชื่อที่ Meet แสดง -> คนที่ลงทะเบียนไว้
        self.assertEqual(speaker, people["ศศิน"]["speaker_id"])
        self.assertEqual(db.get_speaker(speaker)["attendance"], "present")
        self.assertEqual(db.record_room_names(mid, ["Thanawee BOONKERD"]), {"marked": [], "new": []})   # ประธานเข้าร่วมอยู่แล้ว ไม่สร้างซ้ำ
        header = service.get_detail(self.owner, mid)["header"]
        self.assertEqual(header["chair"], "ธนาวีร์ บุญเกิด")                                  # รายงานใช้ชื่อไทย ไม่ใช่ชื่อใน Meet
        rows = [{"speaker_id": p["speaker_id"], "display_name": p["display_name"], "email": p["email"] or "", "role": p["role"],
                 "attendance": p["attendance"], "absence_reason": "", "position": "", "meet_alias": p["meet_alias"] or ""}
                for p in db.list_speakers(mid)]
        next(r for r in rows if r["display_name"] == "ภาม")["meet_alias"] = "Pharm S"        # แก้ชื่อใน Meet ผ่านตาราง
        self.assertEqual(service.apply_people_table(self.owner, mid, rows)["errors"], [])
        self.assertEqual(next(p for p in db.list_speakers(mid) if p["display_name"] == "ภาม")["meet_alias"], "Pharm S")

    def test_action_table_rows_are_tall_enough_for_wrapped_text_and_dates_are_thai(self):
        from fpdf import FPDF
        from reports import pdf_report
        pdf = pdf_report._ReportPDF(draft=False, watermark="")
        pdf.add_page()
        pdf.set_font(pdf_report.FONT, "", 14)
        width = 290
        inner = width - 2 * pdf_report.CELL_PAD
        text = "เตรียมพื้นที่ทำงานให้เรียบร้อยเพื่อรองรับผู้ตรวจจากส่วนกลางในสัปดาห์หน้า"
        # ข้อความที่พอดีช่องเต็มความกว้าง แต่ยาวเกินความกว้างข้างในช่อง ต้องนับเป็น 2 บรรทัดเพราะตอนวาดใช้ความกว้างข้างใน
        fit = ""
        for ch in text:
            if pdf.get_string_width(fit + ch) > inner:
                break
            fit += ch
        spill = fit + text[len(fit):len(fit) + 3]
        self.assertEqual(pdf_report._cell_lines(pdf, width, fit, 20.0), 1)
        self.assertGreaterEqual(pdf_report._cell_lines(pdf, width, spill, 20.0), 2)
        self.assertEqual(pdf_report._fmt_due({"due_date": "2026-10-16"}), "16 ตุลาคม 2569")
        self.assertEqual(pdf_report._fmt_due({"due_date": "2026-10-16", "due_time": "13:00", "due_time_end": "14:00"}),
                         "16 ตุลาคม 2569 13:00-14:00")
        self.assertEqual(pdf_report._fmt_due({"due_date": None}), "-")

    def test_pdf_follows_the_meeting_minutes_form(self):
        import io
        from pypdf import PdfReader
        mid = self.new_meeting(people=[
            {"display_name": "ต้น", "role": "chair", "attendance": "present"},
            {"display_name": "ฟ้า", "role": "secretary", "attendance": "present"},
            {"display_name": "แพร", "attendance": "absent", "absence_reason": "ติดภารกิจ"},
            {"display_name": "คุณสมชาย", "role": "guest", "attendance": "present"},
        ])
        meeting = db.get_meeting(mid)
        content = {"agenda": [{"title": "งบประมาณ", "discussion": "เสนอ 3,000 บาท", "resolution": "อนุมัติ"}],
                   "other_matters": None, "summary": "สรุป",
                   "action_items": [{"description": "ทำใบเบิก", "assignee": "ฟ้า", "due_date": "2026-10-12"}]}
        from reports import pdf_report
        data = pdf_report.build_minutes_pdf(meeting, db.list_speakers(mid), content)
        text = "\n".join(pg.extract_text() for pg in PdfReader(io.BytesIO(data)).pages)
        order = ["ผู้มาประชุม", "ผู้เข้าร่วมประชุม", "ผู้ไม่มาประชุม", "แพร", "ติดภารกิจ", "เริ่มประชุมเวลา", "ประธานกล่าวเปิดการประชุม",
                 "ระเบียบวาระที่ 1", "เรื่องแจ้งให้ที่ประชุมทราบ", "ระเบียบวาระที่ 2", "เรื่องรับรองรายงานการประชุม", "ระเบียบวาระที่ 3",
                 "เรื่องสืบเนื่อง", "ระเบียบวาระที่ 4", "เรื่องพิจารณา", "4.1", "4.2", "4.2.1", "งบประมาณ",
                 "ระเบียบวาระที่ 5", "งานที่ได้รับมอบหมาย", "เลิกประชุมเวลา", "ผู้บันทึกรายงานการประชุม", "ผู้ตรวจรายงานการประชุม"]
        pos = -1
        for marker in order:
            nxt = text.find(marker, pos + 1)
            self.assertGreater(nxt, pos, f"ไม่พบ/ลำดับผิด: {marker!r}\n{text}")
            pos = nxt
        self.assertIn("เมื่อวันที่", text)
        self.assertNotIn("สรุปภาพรวมการประชุม", text)       # แบบฟอร์มไม่มีส่วนนี้ (ตารางงานเก็บไว้)

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

    def test_people_table_leaves_meet_names_alone_and_rematches_after_the_english_name_is_added(self):
        mid = self.new_meeting()
        silent = db.get_or_create_speaker(mid, "BOZZISX")                      # Meet เห็นชื่อนี้ก่อนที่จะมีใครกรอกชื่อภาษาอังกฤษ
        room_only = db.get_or_create_speaker(mid, "Someone Else")
        rows = [r for r in self.table_rows(mid) if r.get("speaker_id") not in (silent, room_only)]   # ตารางในหน้าเว็บไม่มีแถวพบจาก Meet
        result = service.apply_people_table(self.owner, mid, rows)             # บันทึกโดยไม่แก้อะไร
        self.assertEqual((result["deleted"], result["matched"], result["errors"]), (0, 0, []))
        self.assertEqual({db.get_speaker(silent)["display_name"], db.get_speaker(room_only)["display_name"]},
                         {"BOZZISX", "Someone Else"})                          # ชื่อจาก Meet ไม่ถูกลบเพราะไม่อยู่ในตาราง
        bob = next(r for r in rows if r["display_name"] == "Bob")
        bob["meet_alias"] = "bozzisx"                                          # พิมพ์เล็ก ตรงกับ "BOZZISX" โดยไม่สนตัวพิมพ์
        result = service.apply_people_table(self.owner, mid, rows)
        self.assertEqual(result["matched"], 1)
        self.assertIsNone(db.get_speaker(silent))                              # รวมเข้ากับ Bob แล้ว
        self.assertIsNotNone(db.get_speaker(room_only))                        # ที่ยังไม่ตรงคงอยู่

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

    def test_verify_turns_unconfirmed_people_into_absent_so_the_database_matches_the_report(self):
        mid = self.new_meeting()          # Alice พูดในประชุม -> เข้าร่วมเอง, Bob ไม่ได้พูด ยังเป็น "ยังไม่ยืนยัน"
        self.record(mid)
        status = {p["display_name"]: p["attendance"] for p in db.list_speakers(mid)}
        self.assertEqual((status["Alice"], status["Bob"]), ("present", "invited"))
        out = service.verify_transcript(self.owner, mid)
        self.assertEqual(out["marked_absent"], ["Bob"])
        status = {p["display_name"]: p["attendance"] for p in db.list_speakers(mid)}
        self.assertEqual((status["Alice"], status["Bob"]), ("present", "absent"))
        header = service.get_detail(self.owner, mid)["header"]
        absent_in_report = {a["name"] for a in header["absent"]}
        absent_in_db = {p["display_name"] for p in db.list_speakers(mid) if p["attendance"] == "absent"}
        self.assertEqual(absent_in_report, absent_in_db)           # ฐานข้อมูลกับรายงานตรงกัน
        self.assertEqual(header["unconfirmed_count"], 0)

    def test_verify_keeps_explicit_attendance_and_reports_nothing_when_all_are_settled(self):
        mid = self.new_meeting(people=[
            {"display_name": "สมชาย ใจดี", "role": "chair", "attendance": "present"},
            {"display_name": "สมหญิง", "role": "secretary", "attendance": "present"},
            {"display_name": "Alice", "attendance": "present"},
            {"display_name": "Bob", "attendance": "absent", "absence_reason": "ลาป่วย"}])
        self.record(mid)
        self.assertEqual(service.verify_transcript(self.owner, mid)["marked_absent"], [])
        bob = next(p for p in db.list_speakers(mid) if p["display_name"] == "Bob")
        self.assertEqual((bob["attendance"], bob["absence_reason"]), ("absent", "ลาป่วย"))

    def test_approve_also_settles_people_added_after_verification(self):
        mid = self.draft()
        service.add_person(self.owner, mid, "Carol")                # เพิ่มทีหลัง ค่าเริ่มต้น = ยังไม่ยืนยัน
        self.assertEqual(next(p for p in db.list_speakers(mid) if p["display_name"] == "Carol")["attendance"], "invited")
        service.approve_report(self.owner, mid, confirm_warnings=True)
        self.assertEqual(next(p for p in db.list_speakers(mid) if p["display_name"] == "Carol")["attendance"], "absent")

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
        self.assertIsNone(item["google_calendar_event_id"])

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
            body["action_items"][0]["due_date"] = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()   # วันประชุมคือวันนี้ กำหนดส่งต้องไม่อยู่ก่อนวันประชุม
            return json.dumps(body)
        service.generate_report(self.owner, mid, generate=clean_ai)
        view = service.get_report_view(self.owner, mid)
        left = view["report"]["content"]["warnings"] + view["header_warnings"]
        self.assertEqual(left, [], left)
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

    def calendar_env(self, fake):
        return mock.patch.object(calendar_sync, "build", lambda *a, **k: fake), \
            mock.patch.object(calendar_sync.calendar_auth, "get_credentials", lambda uid: object())

    def test_approve_and_send_creates_events_for_dated_items_with_attendees_and_stores_links(self):
        mid = self.draft()                       # งาน 1: ส่งรายงานความก้าวหน้า (Alice, มีวันที่) งาน 2: ไม่มีวันที่
        fake = FakeCalendar()
        with self.assertRaises(ServiceError) as cm:
            service.sync_calendar(self.owner, mid)                                   # ยังไม่อนุมัติ
        self.assertEqual(cm.exception.kind, "conflict")
        p1, p2 = self.calendar_env(fake)
        with p1, p2:
            results = service.approve_and_send(self.owner, mid, confirm_warnings=True)
            self.assertEqual([(r["ok"], r["action"]) for r in results], [(True, "created")])   # งานไม่มีวันที่ถูกข้าม ไม่เดาวัน
            body, send = fake.inserted[0]
            self.assertEqual((body["attendees"], send), ([{"email": "alice@x.com"}], "none"))  # ค่าเริ่มต้นไม่ส่งอีเมลเชิญจริง
            self.assertEqual(body["start"]["dateTime"], "2026-10-09T13:00:00")
            self.assertEqual(db.get_meeting(mid)["status"], "approved")
            items = db.get_report(mid)["content"]["action_items"]
            self.assertEqual((items[0]["google_calendar_event_id"], items[0]["google_calendar_link"]),
                             ("evt1", "https://calendar.google.com/event?eid=evt1"))
            self.assertIsNone(items[1]["google_calendar_event_id"])
            self.assertEqual(service.sync_calendar(self.owner, mid), [])                       # ส่งครบแล้ว ไม่ส่งซ้ำ
            self.assertEqual(len(fake.inserted), 1)
            with self.assertRaises(ServiceError) as cm:                                         # คนอื่นแตะไม่ได้
                service.sync_calendar(self.other, mid)
            self.assertEqual(cm.exception.kind, "not_found")

    def test_invite_emails_only_when_asked(self):
        mid = self.draft()
        fake = FakeCalendar()
        p1, p2 = self.calendar_env(fake)
        with p1, p2:
            service.approve_and_send(self.owner, mid, confirm_warnings=True, send_invites=True)
        self.assertEqual(fake.inserted[0][1], "all")

    def test_approval_survives_calendar_failure_and_sending_can_be_retried(self):
        mid = self.draft()
        results = service.approve_and_send(self.owner, mid, confirm_warnings=True)          # ยังไม่เชื่อมต่อ Calendar
        self.assertEqual(db.get_meeting(mid)["status"], "approved")                         # ไม่ย้อนการอนุมัติ
        self.assertTrue(results and all(not r["ok"] and "ยังไม่เชื่อมต่อ" in r["error"] for r in results))
        fake = FakeCalendar()
        p1, p2 = self.calendar_env(fake)
        with p1, p2:                                                                         # เชื่อมต่อแล้วกดส่งซ้ำ
            self.assertEqual([r["ok"] for r in service.sync_calendar(self.owner, mid)], [True])
        self.assertEqual(len(fake.inserted), 1)

    def test_reopen_deletes_sent_events_and_clears_marks_then_approval_sends_again(self):
        mid = self.draft()
        fake = FakeCalendar()
        p1, p2 = self.calendar_env(fake)
        with p1, p2:
            service.approve_and_send(self.owner, mid, confirm_warnings=True)
            out = service.reopen_report(self.owner, mid)
            self.assertEqual((out["deleted"], out["failed"], fake.deleted), (1, [], ["evt1"]))   # นัดถูกลบออกจาก Calendar
            self.assertEqual(db.get_meeting(mid)["status"], "draft")
            self.assertFalse(any(i["google_calendar_event_id"] or i["google_calendar_link"]
                                 for i in db.get_report(mid)["content"]["action_items"]))
            service.approve_and_send(self.owner, mid, confirm_warnings=True)                     # อนุมัติใหม่ = ส่งใหม่
            self.assertEqual(len(fake.inserted), 2)

    def test_reopen_is_blocked_when_events_cannot_be_deleted_unless_forced(self):
        mid = self.draft()
        fake = FakeCalendar()
        p1, p2 = self.calendar_env(fake)
        with p1, p2:
            service.approve_and_send(self.owner, mid, confirm_warnings=True)
        with mock.patch.object(calendar_sync.calendar_auth, "get_credentials", lambda uid: None):    # เชื่อมต่อหลุด
            with self.assertRaises(ServiceError) as cm:
                service.reopen_report(self.owner, mid)
            self.assertEqual(cm.exception.kind, "calendar_failed")
            self.assertIn("ยังไม่เชื่อมต่อ", cm.exception.extra["failed"][0]["error"])
            self.assertEqual(db.get_meeting(mid)["status"], "approved")                              # ไม่ยกเลิกอนุมัติ
            out = service.reopen_report(self.owner, mid, force=True)                                 # ยืนยันให้ยกเลิกต่อ
            self.assertEqual((len(out["failed"]), db.get_meeting(mid)["status"]), (1, "draft"))
        self.assertFalse(any(i["google_calendar_event_id"] for i in db.get_report(mid)["content"]["action_items"]))

    def test_event_already_deleted_in_google_is_not_an_error_on_reopen(self):
        mid = self.draft()
        fake = FakeCalendar()
        p1, p2 = self.calendar_env(fake)
        with p1, p2:
            service.approve_and_send(self.owner, mid, confirm_warnings=True)
            fake.delete_error = 404                                                  # ผู้ใช้ลบนัดใน Google ไปเองแล้ว
            out = service.reopen_report(self.owner, mid)
        self.assertEqual((out["deleted"], out["failed"]), (0, []))
        self.assertEqual(db.get_meeting(mid)["status"], "draft")
        with p1, p2:
            fake.delete_error = 500                                                  # error อื่นต้องรายงาน ไม่กลบเป็นสำเร็จ
            service.approve_and_send(self.owner, mid, confirm_warnings=True)
            with self.assertRaises(ServiceError):
                service.reopen_report(self.owner, mid)

    def test_only_the_approver_can_delete_their_calendar_events(self):
        mid = self.draft()
        fake = FakeCalendar()
        p1, p2 = self.calendar_env(fake)
        with p1, p2:
            service.approve_and_send(self.owner, mid, confirm_warnings=True)
            report = db.get_report(mid)
            with mock.patch.object(db, "get_report", lambda m: {**report, "approved_by": self.other["user_id"]}):
                with self.assertRaises(ServiceError) as cm:                           # ผู้กดอนุมัติเป็นคนอื่น: นัดอยู่ในปฏิทินเขา
                    service.reopen_report(self.owner, mid)
        self.assertEqual(cm.exception.kind, "calendar_failed")
        self.assertEqual(fake.deleted, [])                                            # ไม่ลองลบด้วยปฏิทินของคนอื่น (กัน 404 หลอกว่าลบแล้ว)

    def test_calendar_token_is_per_user(self):
        db.save_calendar_token(self.owner["user_id"], '{"a": 1}')
        self.assertTrue(service.calendar_connected(self.owner))
        self.assertFalse(service.calendar_connected(self.other))


if __name__ == "__main__":
    unittest.main()
