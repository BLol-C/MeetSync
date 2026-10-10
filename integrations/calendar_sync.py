"""เขียน action item ของรายงานการประชุมที่อนุมัติแล้ว กลับไปเป็น event ใน Google Calendar ของผู้ใช้"""

import datetime
import os

from googleapiclient.discovery import build

from integrations import calendar_auth
import db
from reports.summarizer import match_participant_name

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


def _calendar_service(user_id: int):
    creds = calendar_auth.get_credentials(user_id)
    if creds is None:
        raise RuntimeError("ยังไม่เชื่อมต่อ Google Calendar")
    return build("calendar", "v3", credentials=creds)


def _http_status(exc: Exception) -> int | None:
    return getattr(getattr(exc, "resp", None), "status", None)


def _patch_body(body: dict, email: str | None) -> dict:
    """เนื้อหาสำหรับอัปเดตนัดเดิม (PATCH แบบ merge): ใส่ null ให้ฟิลด์ของอีกแบบ (วันเต็มวัน/วัน+เวลา) กันค้างค่าเก่า
    และส่งรายชื่อผู้ร่วมงานทุกครั้ง (ว่าง = ล้างคนเดิม) เพื่อให้ตรงกับผู้รับผิดชอบปัจจุบัน"""
    out = dict(body)
    for key in ("start", "end"):
        part = dict(body[key])
        if "date" in part:
            part.update({"dateTime": None, "timeZone": None})
        else:
            part["date"] = None
        out[key] = part
    out["attendees"] = [{"email": email}] if email else []
    return out


def push_task(task_id: int, user_id: int, send_invites: bool = False, service=None) -> dict:
    """ส่งงาน (calendar_tasks) เข้า Google Calendar ของ user_id: ยังไม่เคยส่ง = สร้างนัดใหม่, ส่งแล้วแต่แก้ภายหลัง = อัปเดตนัดเดิม,
    ส่งแล้วและไม่ได้แก้ = ไม่ทำอะไร คืน {"action": created|updated|unchanged, "event_id", "link"}

    service ฉีดเข้ามาได้ (ไว้ทดสอบ) ผู้รับผิดชอบที่มีอีเมลในรายชื่อจะถูกเพิ่มเป็น attendee; ส่งอีเมลเชิญจริงต่อเมื่อ send_invites
    (ค่าเริ่มต้นไม่ส่ง กันอีเมลเชิญหลุดไปหาคนจริงตอนทดลอง/เดโม) ถ้านัดเดิมถูกลบใน Google ไปแล้ว จะสร้างใหม่ให้
    """
    task = db.get_calendar_task(task_id)
    if task is None:
        raise ValueError("ไม่พบงานนี้")
    if task["sync_status"] == "synced":
        return {"action": "unchanged", "event_id": task["google_calendar_event_id"], "link": task["google_calendar_link"]}
    email = attendee_email(db.list_speakers(task["meeting_id"]), task["assignee"])
    body = build_event_body(task, email)          # ไม่มีวัน -> ValueError ก่อนจะแตะ Google
    service = service or _calendar_service(user_id)
    send = "all" if send_invites else "none"
    event, action = None, "created"
    if task["google_calendar_event_id"]:
        try:
            event = service.events().patch(calendarId="primary", eventId=task["google_calendar_event_id"],
                                           body=_patch_body(body, email), sendUpdates=send).execute()
            action = "updated"
        except Exception as e:  # noqa: BLE001
            if _http_status(e) not in (404, 410):    # นัดเดิมหายจาก Google แล้ว -> สร้างใหม่; ข้อผิดพลาดอื่นให้ผู้เรียกเห็น
                raise
    if event is None:
        event = service.events().insert(calendarId="primary", body=body, sendUpdates=send).execute()
    link = event.get("htmlLink")
    db.mark_calendar_task_synced(task_id, event["id"], link, task["content_version"])
    return {"action": action, "event_id": event["id"], "link": link}


def delete_task_event(task_id: int, user_id: int, service=None) -> bool:
    """ลบนัดของงานนี้ออกจาก Google Calendar (ไม่ลบแถวงานใน DB) คืน True ถ้าลบจริง — ไม่เคยส่ง หรือนัดหายไปแล้ว = ไม่มีอะไรให้ลบ"""
    task = db.get_calendar_task(task_id)
    if task is None or not task["google_calendar_event_id"]:
        return False
    service = service or _calendar_service(user_id)
    try:
        service.events().delete(calendarId="primary", eventId=task["google_calendar_event_id"], sendUpdates="none").execute()
    except Exception as e:  # noqa: BLE001
        if _http_status(e) in (404, 410):
            return False
        raise
    return True
