"""ทดสอบชั้นคุณภาพของ AI ที่ไม่ต้องเรียก Gemini จริง: validate_minutes, grounding, chunking, prompt, retry"""

import datetime
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reports import summarizer  # noqa: E402

MEETING = datetime.datetime(2026, 10, 1, 9, 30)   # วันพฤหัสบดี
PARTS = [
    {"participant_id": 1, "display_name": "สมชาย ใจดี", "role": "chair", "attendance": "present"},
    {"participant_id": 2, "display_name": "Alice", "role": "attendee", "attendance": "present"},
]
ROWS = [
    {"display_name": "สมชาย ใจดี", "text": "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท", "spoken_at": MEETING},
    {"display_name": "Alice", "text": "เห็นด้วยค่ะ ดิฉันจะส่งรายงานความก้าวหน้าภายในวันศุกร์หน้า", "spoken_at": MEETING},
]
TEXTS = [r["text"] for r in ROWS]


def good_body(**over):
    body = {
        "summary": "ที่ประชุมอนุมัติงบประมาณ",
        "agenda": [{
            "section": "consider_new", "title": "งบประมาณ", "discussion": "นายสมชายเสนอให้อนุมัติงบ",
            "resolution": "อนุมัติงบสองหมื่นบาท", "evidence": ["ผมเสนอให้อนุมัติงบสองหมื่นบาท"],
        }],
        "other_matters": None,
        "action_items": [{
            "description": "ส่งรายงานความก้าวหน้า", "assignee": "Alice", "due_date": "2026-10-09",
            "due_time": None, "due_time_end": None, "evidence": ["จะส่งรายงานความก้าวหน้าภายในวันศุกร์หน้า"],
        }],
    }
    body.update(over)
    return body


def codes(result):
    return sorted(w["code"] for w in result["warnings"])


class ValidateTests(unittest.TestCase):
    def check(self, body):
        return summarizer.validate_minutes(body, PARTS, TEXTS, MEETING.date())

    def test_clean_minutes_have_no_warnings(self):
        r = self.check(good_body())
        self.assertEqual(r["warnings"], [])
        self.assertIs(r["agenda"][0]["grounded"], True)
        self.assertIs(r["action_items"][0]["grounded"], True)

    def test_fabricated_evidence_is_flagged(self):
        body = good_body()
        body["agenda"][0]["evidence"] = ["ที่ประชุมมีมติเอกฉันท์ให้ซื้อเซิร์ฟเวอร์ใหม่ทั้งหมดสิบเครื่อง"]
        r = self.check(body)
        self.assertIn("ungrounded", codes(r))
        self.assertIs(r["agenda"][0]["grounded"], False)

    def test_resolution_without_evidence_is_flagged_but_plain_item_is_not(self):
        body = good_body()
        body["agenda"][0]["evidence"] = []
        self.assertIn("no_evidence", codes(self.check(body)))
        body["agenda"][0]["resolution"] = None          # เพื่อทราบ ไม่มีมติ -> ไม่ต้องมีหลักฐาน
        self.assertNotIn("no_evidence", codes(self.check(body)))

    def test_grounding_ignores_thai_spacing(self):
        sh = summarizer.transcript_shingles(["ส่งรายงานวันศุกร์หน้า"])
        self.assertEqual(summarizer.grounding_score("ส่งรายงาน วันศุกร์ หน้า", sh), 1.0)
        self.assertLess(summarizer.grounding_score("ซื้อคอมพิวเตอร์เครื่องใหม่", sh), 0.3)
        self.assertEqual(summarizer.grounding_score("", sh), 0.0)

    def test_assignee_must_be_a_participant(self):
        body = good_body()
        body["action_items"][0]["assignee"] = "บุคคลที่ไม่มีในห้อง"
        r = self.check(body)
        self.assertIn("assignee_unknown", codes(r))
        self.assertEqual(r["action_items"][0]["assignee"], "บุคคลที่ไม่มีในห้อง")   # คงค่าไว้ให้คนแก้ ไม่ลบเงียบๆ

    def test_assignee_is_normalised_to_participant_name(self):
        body = good_body()
        body["action_items"][0]["assignee"] = "คุณสมชาย"          # AI ใส่คำนำหน้ามา
        r = self.check(body)
        self.assertEqual(r["action_items"][0]["assignee"], "สมชาย ใจดี")
        self.assertNotIn("assignee_unknown", codes(r))

    def test_missing_assignee_warns(self):
        body = good_body()
        body["action_items"][0]["assignee"] = None
        self.assertIn("assignee_missing", codes(self.check(body)))

    def test_bad_date_is_cleared_and_flagged(self):
        body = good_body()
        body["action_items"][0]["due_date"] = "2026-13-45"
        r = self.check(body)
        self.assertIn("bad_date", codes(r))
        self.assertIsNone(r["action_items"][0]["due_date"])

    def test_date_before_meeting_is_flagged_but_kept(self):
        body = good_body()
        body["action_items"][0]["due_date"] = "2026-09-01"
        r = self.check(body)
        self.assertIn("date_before_meeting", codes(r))
        self.assertEqual(r["action_items"][0]["due_date"], "2026-09-01")

    def test_times(self):
        body = good_body()
        body["action_items"][0].update(due_time="9:05", due_time_end="08:00")
        r = self.check(body)
        self.assertEqual(r["action_items"][0]["due_time"], "09:05")        # ทำให้เป็นรูปแบบ HH:MM
        self.assertIn("time_order", codes(r))
        body["action_items"][0].update(due_time="25:99", due_time_end=None)
        r = self.check(body)
        self.assertIn("bad_time", codes(r))
        self.assertIsNone(r["action_items"][0]["due_time"])
        body["action_items"][0].update(due_date=None, due_time="10:00")
        self.assertIn("time_without_date", codes(self.check(body)))

    def test_empty_agenda_and_summary_warn(self):
        r = self.check(good_body(agenda=[], summary="  "))
        self.assertTrue({"no_agenda", "empty_summary"} <= set(codes(r)))

    def test_bare_number_after_an_approximation_word_is_flagged_but_units_are_fine(self):
        bad = good_body()
        bad["agenda"][0]["discussion"] = "ลองรายงานว่าโครงการเสร็จไปแล้วประมาณ 1 ยังเหลือส่วนรายงานกับส่วนค้นหา"
        result = self.check(bad)
        self.assertEqual(codes(result), ["unclear_number"])
        self.assertIn("ประมาณ 1", result["warnings"][0]["message"])
        self.assertEqual(result["warnings"][0]["path"], "agenda[0].discussion")
        for ok in ("จัดซื้อประมาณ 1 เครื่อง งบประมาณ 25,000 บาท", "เสร็จไปแล้วประมาณ 50%", "ใช้เวลากว่า 2 สัปดาห์",
                   "มีผู้เข้าร่วมราว 30 คน", "งบกว่า 2 ล้านบาท", "ไม่มีตัวเลขเลย"):
            good = good_body()
            good["agenda"][0]["discussion"] = ok
            self.assertEqual(codes(self.check(good)), [], ok)
        action = good_body()
        action["action_items"][0]["description"] = "เสร็จภายในประมาณ 3"
        self.assertEqual(codes(self.check(action)), ["unclear_number"])

    def test_revalidation_is_idempotent_for_human_edits(self):
        first = self.check(good_body())
        again = summarizer.validate_minutes(first, PARTS, TEXTS, MEETING.date())
        again.pop("warnings"); first.pop("warnings")
        self.assertEqual(first, again)


class PromptTests(unittest.TestCase):
    def test_fill_is_single_pass(self):
        out = summarizer.fill("ชื่อ: {participants}\nข้อความ: {transcript}",
                              participants="{transcript}", transcript="SECRET {participants}")
        self.assertEqual(out, "ชื่อ: {transcript}\nข้อความ: SECRET {participants}")

    def test_prompt_files_only_use_known_placeholders(self):
        import re
        known = {"meeting_title", "weekday", "date", "participants", "source_label", "source_title", "transcript"}
        known_map = {"part", "total", "date", "participants", "transcript"}
        for name, allowed in ((summarizer.PROMPT_NAME, known), (summarizer.MAP_PROMPT_NAME, known_map)):
            text = summarizer.load_prompt(name)
            self.assertLessEqual(set(re.findall(r"\{(\w+)\}", text)), allowed, name)

    def test_split_chunks_respects_limit_and_keeps_order(self):
        rows = [{"display_name": "A", "text": "x" * 100, "spoken_at": MEETING} for _ in range(10)]
        chunks = summarizer.split_chunks(rows, 300)
        self.assertTrue(all(len(c) >= 1 for c in chunks))
        self.assertEqual(sum(len(c) for c in chunks), 10)
        self.assertGreater(len(chunks), 2)


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def make(self, bodies):
        bodies = list(bodies)

        def generate(prompt, schema=None):
            self.calls.append((prompt, schema))
            if schema is None:
                return "บันทึกย่อของช่วงนี้"
            return bodies.pop(0) if len(bodies) > 1 else bodies[0]
        return generate

    def test_single_pass(self):
        gen = self.make([json.dumps(good_body())])
        out = summarizer.generate_minutes_content(ROWS, PARTS, "ประชุมโครงการ", MEETING, gen)
        self.assertEqual(len(self.calls), 1)
        prompt, schema = self.calls[0]
        self.assertIs(schema, summarizer.MinutesBodyAI)               # บังคับ schema จริง ไม่ใช่แค่ขอใน prompt
        self.assertIn("สมชาย ใจดี (ประธาน)", prompt)
        self.assertIn("วันพฤหัสบดีที่ 2026-10-01", prompt)
        self.assertIn("[09:30] Alice: เห็นด้วยค่ะ", prompt)
        self.assertNotRegex(prompt, r"\{(meeting_title|weekday|date|participants|transcript|source_label)\}")
        self.assertEqual(out["warnings"], [])

    def test_ai_chooses_the_agenda_section_and_it_is_kept(self):
        body = good_body(agenda=[
            {"section": "inform", "title": "แจ้งงบประมาณ", "discussion": "ประธานฯ แจ้งต่อที่ประชุมว่ามีงบ",
             "resolution": None, "evidence": []},
            good_body()["agenda"][0],
        ])
        out = summarizer.generate_minutes_content(ROWS, PARTS, None, MEETING, self.make([json.dumps(body)]))
        self.assertEqual([a["section"] for a in out["agenda"]], ["inform", "consider_new"])
        self.assertEqual([a["section"] for a in summarizer.for_storage(out)["agenda"]], ["inform", "consider_new"])

    def test_section_outside_the_two_ai_choices_is_rejected_by_the_schema(self):
        body = good_body()
        body["agenda"][0]["section"] = "approve_prev"            # วาระ 2 ระบบเติมเอง ไม่ใช่งานของ AI
        with self.assertRaises(ValueError):
            summarizer.generate_minutes_content(ROWS, PARTS, None, MEETING, self.make([json.dumps(body)]))

    def test_prompt_v4_tells_the_ai_not_to_invent_resolutions_and_to_write_by_speaker(self):
        text = summarizer.load_prompt("minutes_v4")
        for phrase in ("รายงานต่อที่ประชุมว่า", "ประธานฯ กล่าวว่า", "ห้ามเติม \"รับทราบ\" หรือ \"เห็นชอบ\" เอง",
                       '"consider_new"', '"inform"'):
            self.assertIn(phrase, text)
        for phrase in ("เรื่องนั้นโดยตรง", "ผู้สรุปมติ ไม่ใช่ผู้เสนอ", "ห้ามเพิ่มรายละเอียด", "ตรงกันทุกจุดของรายงาน"):   # บทเรียนจากการทดสอบกับ Meet จริง
            self.assertIn(phrase, text)
        self.assertEqual(summarizer.PROMPT_NAME, "minutes_v4")

    def test_long_meeting_uses_map_reduce(self):
        old = summarizer.SINGLE_PASS_CHARS, summarizer.CHUNK_CHARS
        summarizer.SINGLE_PASS_CHARS, summarizer.CHUNK_CHARS = 120, 100
        try:
            gen = self.make([json.dumps(good_body())])
            summarizer.generate_minutes_content(ROWS * 3, PARTS, None, MEETING, gen)
        finally:
            summarizer.SINGLE_PASS_CHARS, summarizer.CHUNK_CHARS = old
        map_calls = [c for c in self.calls if c[1] is None]
        final = [c for c in self.calls if c[1] is not None]
        self.assertGreaterEqual(len(map_calls), 2)
        self.assertEqual(len(final), 1)
        self.assertIn("=== ช่วงที่ 1/", final[0][0])
        self.assertIn("บันทึกย่อของช่วงนี้", final[0][0])
        self.assertNotIn("Alice: เห็นด้วยค่ะ", final[0][0])           # ขั้นสุดท้ายเห็นแต่บันทึกย่อ ไม่ใช่ transcript ดิบ

    def test_bad_ai_output_is_retried_once(self):
        gen = self.make(["นี่ไม่ใช่ JSON", json.dumps(good_body())])
        summarizer.generate_minutes_content(ROWS, PARTS, None, MEETING, gen)
        self.assertEqual(len(self.calls), 2)

    def test_bad_ai_output_twice_raises(self):
        gen = self.make(["{}"])                                        # ขาดฟิลด์บังคับทั้งหมด
        with self.assertRaisesRegex(ValueError, "รูปแบบที่ไม่ถูกต้อง"):
            summarizer.generate_minutes_content(ROWS, PARTS, None, MEETING, gen)
        self.assertEqual(len(self.calls), 2)

    def test_empty_transcript_raises(self):
        with self.assertRaises(ValueError):
            summarizer.generate_minutes_content([], PARTS, None, MEETING, self.make([json.dumps(good_body())]))


if __name__ == "__main__":
    unittest.main()
