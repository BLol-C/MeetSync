"""เขียน action item ของรายงานการประชุมที่อนุมัติแล้ว กลับไปเป็น event ใน Google Calendar ของผู้ใช้"""

import datetime
import os

from googleapiclient.discovery import build

import calendar_auth
import db
from summarizer import match_participant_name

TIMEZONE = "Asia/Bangkok"
DEFAULT_DURATION = datetime.timedelta(hours=1)


def _parse_hhmm(value: str | None) -> datetime.time | None:
    if not value:
        return None
    hour, minute = value.split(":")[:2]
    return datetime.time(int(hour), int(minute))


def attendee_email(participants: list[dict], assignee: str | None) -> str | None:
    """อีเมลของผู้รับผิดชอบจากรายชื่อผู้เข้าร่วม (ต้องจับคู่ชื่อได้คนเดียวและมีอีเมลเท่านั้น)"""
    name = match_participant_name(assignee, participants)
    if not name:
        return None
    for p in participants:
        if p["display_name"] == name:
            return p.get("email") or None
    return None


def build_event_body(item: dict, email: str | None = None) -> dict:
    """สร้างเนื้อหา Calendar event จาก action item (ฟังก์ชันล้วน ทดสอบได้โดยไม่ต้องต่อ Google)

    - ไม่มีวัน -> ValueError (เดิมเดาเป็น "พรุ่งนี้" ซึ่งทำให้นัดผิดวันโดยไม่มีใครรู้)
    - มีเวลา: ไม่ระบุเวลาจบให้ยาว 1 ชม.; เวลาจบไม่หลังเวลาเริ่ม (เช่น 23:30-00:30) ถือว่าข้ามไปวันถัดไป
    - ไม่มีเวลา: event ทั้งวัน — Google นับ end.date แบบ "ไม่รวมวันนั้น" จึงต้องเป็นวันถัดไป
    """
    if not item.get("due_date"):
        raise ValueError("งานนี้ยังไม่มีกำหนดวัน — ระบุวันที่ในรายงานก่อนจึงจะส่งเข้า Calendar ได้")
    day = item["due_date"] if isinstance(item["due_date"], datetime.date) else datetime.date.fromisoformat(
        str(item["due_date"])
    )
    start_t = _parse_hhmm(item.get("due_time"))
    end_t = _parse_hhmm(item.get("due_time_end"))

    detail = []
    if item.get("assignee"):
        detail.append(f"ผู้รับผิดชอบ: {item['assignee']}")

    if start_t:
        start_dt = datetime.datetime.combine(day, start_t)
        if end_t:
            end_dt = datetime.datetime.combine(day, end_t)
            if end_dt <= start_dt:
                end_dt += datetime.timedelta(days=1)
            detail.append(f"กำหนด: {day} เวลา {start_t:%H:%M} - {end_t:%H:%M} น.")
        else:
            end_dt = start_dt + DEFAULT_DURATION
            detail.append(f"กำหนด: {day} เวลา {start_t:%H:%M} น.")
        start = {"dateTime": start_dt.isoformat(timespec="seconds"), "timeZone": TIMEZONE}
        end = {"dateTime": end_dt.isoformat(timespec="seconds"), "timeZone": TIMEZONE}
    else:
        detail.append(f"กำหนด: {day}")
        start = {"date": day.isoformat()}
        end = {"date": (day + datetime.timedelta(days=1)).isoformat()}

    body = {
        "summary": item["description"],
        "description": "\n".join(detail) + "\n\nสร้างจาก MeetSync",
        "start": start,
        "end": end,
    }
    if email:
        body["attendees"] = [{"email": email}]
    return body


def sync_action_item(action_item_id: int, user_id: int, service=None) -> str:
    """สร้าง Calendar event จาก action item นี้ในปฏิทินของ user_id บันทึก event id กลับ DB คืนค่า event id

    service ฉีดเข้ามาได้ (ไว้ทดสอบ) ค่าเริ่มต้นสร้างจาก token ของผู้ใช้
    ผู้รับผิดชอบที่มีอีเมลในรายชื่อจะถูกเพิ่มเป็น attendee; ส่งอีเมลเชิญจริงต่อเมื่อตั้ง CALENDAR_SEND_INVITES=1
    (ค่าเริ่มต้นไม่ส่ง กันอีเมลเชิญหลุดไปหาคนจริงตอนทดลอง/เดโม)
    """
    item = db.get_action_item(action_item_id)
    if item is None:
        raise ValueError("ไม่พบ action item นี้")
    if item["calendar_synced"]:
        return item["google_calendar_event_id"]

    if service is None:
        creds = calendar_auth.get_credentials(user_id)
        if creds is None:
            raise RuntimeError("ยังไม่เชื่อมต่อ Google Calendar")
        service = build("calendar", "v3", credentials=creds)

    meeting_id = db.get_action_item_meeting(action_item_id)
    participants = db.list_participants(meeting_id) if meeting_id else []
    body = build_event_body(item, attendee_email(participants, item["assignee"]))
    send = "all" if os.environ.get("CALENDAR_SEND_INVITES") == "1" else "none"
    event = service.events().insert(calendarId="primary", body=body, sendUpdates=send).execute()
    db.mark_action_item_synced(action_item_id, event["id"])
    return event["id"]
