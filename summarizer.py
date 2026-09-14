"""
สรุปการประชุมด้วย Gemini จาก transcript ที่เก็บไว้ใน MySQL แล้วบันทึกผลกลับลง
ตาราง summaries/action_items

ต้องตั้งค่า GEMINI_API_KEY ใน .env ก่อนใช้ (ขอฟรีได้ที่ https://aistudio.google.com/apikey)
"""

import datetime
import json
import os

from google import genai
from google.genai import types

import db

_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

_THAI_WEEKDAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]

_PROMPT = """คุณเป็นผู้ช่วยสรุปการประชุมภาษาไทย การประชุมนี้จัดขึ้นวัน{weekday}ที่ {date} (ใช้วันนี้เป็น
จุดอ้างอิงตีความคำพูดที่พูดถึงวันแบบสัมพัทธ์ เช่น "พรุ่งนี้"/"วันศุกร์นี้"/"อีก 2 วัน" ให้เป็นวันที่จริง)

จาก transcript ด้านล่าง ให้สรุปเนื้อหาและแยก action item ออกมา ถือว่า "action item" คือทั้งงานที่ต้องทำ
และนัดหมาย/กำหนดการ/เดดไลน์ใดๆ ที่ถูกพูดถึงในที่ประชุม (ไม่ใช่แค่ประโยคที่ขึ้นต้นด้วย "ต้องทำ")
ถ้าในบทสนทนาระบุวันและ/หรือเวลาของเรื่องนั้นไว้ชัดเจน **ต้องแปลงเป็น due_date/due_time เสมอ อย่าตอบ
null ทั้งที่มีข้อมูลอยู่ในบทสนทนา**

ตอบเป็น JSON เท่านั้น ตามรูปแบบนี้ (ห้ามมีข้อความอื่นนอก JSON):
{{
  "executive_summary": "สรุปเนื้อหาการประชุมแบบกระชับ 3-6 ประโยค",
  "action_items": [
    {{
      "description": "งานหรือนัดหมายที่ต้องทำ ระบุรายละเอียดให้ชัดว่าเรื่องอะไร",
      "assignee": "ชื่อผู้รับผิดชอบ หรือ null ถ้าไม่ชัด",
      "due_date": "YYYY-MM-DD หรือ null ถ้าไม่มีกำหนดวันเลย",
      "due_time": "HH:MM แบบ 24 ชม. (เวลาเริ่ม) หรือ null ถ้าไม่มีการระบุเวลา",
      "due_time_end": "HH:MM แบบ 24 ชม. (เวลาสิ้นสุด ถ้าบทสนทนาระบุช่วงเวลาไว้ เช่น '13:00 ถึง 16:00 น.'
        ให้ใส่ 16:00 ที่นี่) หรือ null ถ้าไม่มีการระบุเวลาสิ้นสุด"
    }}
  ]
}}
ถ้าไม่มี action item ให้ตอบ "action_items": []

Transcript:
{transcript}
"""


def _meeting_reference_date(meeting_id: int) -> datetime.datetime:
    meeting = db.get_meeting(meeting_id)
    started_at = meeting["started_at"] if meeting else None
    return started_at or datetime.datetime.now()


def summarize_meeting(meeting_id: int) -> dict:
    """สรุป meeting_id ด้วย Gemini แล้วบันทึกผลลง DB คืนค่า dict ผลสรุป (รวม summary_id)"""
    existing = db.get_summary(meeting_id)
    if existing is not None:
        return existing

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("ยังไม่ได้ตั้งค่า GEMINI_API_KEY ใน .env")

    rows = db.get_transcript(meeting_id)
    if not rows:
        raise ValueError("ไม่มี transcript สำหรับการประชุมนี้ (meeting_id ไม่ถูกต้อง หรือยังไม่มีคำบรรยาย final)")
    transcript_text = "\n".join(f"{r['display_name']}: {r['text']}" for r in rows)

    ref_date = _meeting_reference_date(meeting_id)
    prompt = _PROMPT.format(
        weekday=_THAI_WEEKDAYS[ref_date.weekday()],
        date=ref_date.strftime("%Y-%m-%d"),
        transcript=transcript_text,
    )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    result = json.loads(response.text)

    summary_id = db.insert_summary(meeting_id, result["executive_summary"], _MODEL)
    for item in result.get("action_items", []):
        db.insert_action_item(
            summary_id,
            item.get("description", ""),
            item.get("assignee"),
            item.get("due_date"),
            item.get("due_time"),
            item.get("due_time_end"),
        )

    return db.get_summary(meeting_id)
