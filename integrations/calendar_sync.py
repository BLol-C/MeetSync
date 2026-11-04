"""เขียน action item ของรายงานการประชุมที่อนุมัติแล้ว กลับไปเป็น event ใน Google Calendar ของผู้ใช้"""

import datetime
import os

from googleapiclient.discovery import build

from integrations import calendar_auth
import db
from reports.summarizer import match_participant_name

TIMEZONE = "Asia/Bangkok"
DEFAULT_DURATION = datetime.timedelta(hours=1)
BANGKOK = datetime.timezone(datetime.timedelta(hours=7))     # ไทยไม่มีเวลาออมแสง ใช้ UTC+7 ตรง ๆ (ไม่พึ่งฐานข้อมูลเขตเวลาของเครื่อง)


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
    - มีวันที่สิ้นสุด (due_date_end) = นัดข้ามหลายวัน; วันสิ้นสุดอยู่ก่อนวันเริ่ม -> ValueError; ไม่มี = วันเดียวกับวันเริ่ม
    - มีเวลา: ไม่ระบุเวลาจบให้ยาว 1 ชม.; เวลาจบไม่หลังเวลาเริ่ม (เช่น 23:30-00:30) และไม่ได้ระบุวันสิ้นสุด ถือว่าข้ามไปวันถัดไป
      มีวันสิ้นสุด: เวลาจบอยู่ในวันสิ้นสุด (ไม่มีเวลาจบ = เวลาเริ่มของวันสุดท้ายบวก 1 ชม.)
    - ไม่มีเวลา: event ทั้งวัน (ต่อเนื่องถึงวันสิ้นสุด) — Google นับ end.date แบบ "ไม่รวมวันนั้น" จึงต้องเป็นวันถัดไปจากวันสิ้นสุด
    """
    if not item.get("due_date"):
        raise ValueError("งานนี้ยังไม่มีกำหนดวัน — ระบุวันที่ในรายงานก่อนจึงจะส่งเข้า Calendar ได้")
    day = item["due_date"] if isinstance(item["due_date"], datetime.date) else datetime.date.fromisoformat(
        str(item["due_date"])
    )
    last_day = item.get("due_date_end")
    if last_day:
        last_day = last_day if isinstance(last_day, datetime.date) else datetime.date.fromisoformat(str(last_day))
        if last_day < day:
            raise ValueError(f"วันที่สิ้นสุด ({last_day}) อยู่ก่อนวันที่เริ่ม ({day}) — แก้ในรายงานก่อนจึงจะส่งเข้า Calendar ได้")
    else:
        last_day = day
    multi = last_day != day
    start_t = _parse_hhmm(item.get("due_time"))
    end_t = _parse_hhmm(item.get("due_time_end"))

    detail = []
    if item.get("assignee"):
        detail.append(f"ผู้รับผิดชอบ: {item['assignee']}")

    if start_t:
        start_dt = datetime.datetime.combine(day, start_t)
        if end_t:
            end_dt = datetime.datetime.combine(last_day, end_t)
            if end_dt <= start_dt and not multi:
                end_dt += datetime.timedelta(days=1)
            if end_dt <= start_dt:
                raise ValueError("เวลาสิ้นสุดไม่หลังเวลาเริ่ม — แก้ในรายงานก่อนจึงจะส่งเข้า Calendar ได้")
            if multi:
                detail.append(f"กำหนด: {day} เวลา {start_t:%H:%M} ถึง {last_day} เวลา {end_t:%H:%M} น.")
            else:
                detail.append(f"กำหนด: {day} เวลา {start_t:%H:%M} - {end_t:%H:%M} น.")
        else:
            end_dt = datetime.datetime.combine(last_day, start_t) + DEFAULT_DURATION
            detail.append(f"กำหนด: {day} เวลา {start_t:%H:%M} น." + (f" ถึง {last_day}" if multi else ""))
        start = {"dateTime": start_dt.isoformat(timespec="seconds"), "timeZone": TIMEZONE}
        end = {"dateTime": end_dt.isoformat(timespec="seconds"), "timeZone": TIMEZONE}
    else:
        detail.append(f"กำหนด: {day}" + (f" ถึง {last_day}" if multi else ""))
        start = {"date": day.isoformat()}
        end = {"date": (last_day + datetime.timedelta(days=1)).isoformat()}

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


def create_event(action_item_id: int, user_id: int, send_invites: bool = False, service=None) -> dict:
    """สร้างนัดใน Google Calendar ของ user_id จากงานของรายงาน (action_items) แล้วบันทึก event id + ลิงก์กลับ DB
    งานที่ส่งแล้วไม่ส่งซ้ำ คืน {"action": created|unchanged, "event_id", "link"}

    service ฉีดเข้ามาได้ (ไว้ทดสอบ) ผู้รับผิดชอบที่มีอีเมลในรายชื่อจะถูกเพิ่มเป็น attendee; ส่งอีเมลเชิญจริงต่อเมื่อ send_invites
    (ค่าเริ่มต้นไม่ส่ง กันอีเมลเชิญหลุดไปหาคนจริงตอนทดลอง/เดโม)
    """
    item = db.get_action_item(action_item_id)
    if item is None:
        raise ValueError("ไม่พบงานนี้")
    if item["google_calendar_event_id"]:
        return {"action": "unchanged", "event_id": item["google_calendar_event_id"], "link": item.get("google_calendar_link")}
    meeting_id = db.get_action_item_meeting(action_item_id)
    email = attendee_email(db.list_speakers(meeting_id), item["assignee"]) if meeting_id else None
    body = build_event_body(item, email)          # ไม่มีวัน -> ValueError ก่อนจะแตะ Google
    service = service or _calendar_service(user_id)
    event = service.events().insert(calendarId="primary", body=body, sendUpdates="all" if send_invites else "none").execute()
    link = event.get("htmlLink")
    # จดว่าส่งเชิญจริงเฉพาะเมื่อขอส่ง และนัดนี้มีผู้รับเชิญ (ไม่มีอีเมล = ไม่มีอีเมลเชิญไปไหน)
    db.mark_action_item_synced(action_item_id, event["id"], link, invite_sent=bool(send_invites and email))
    return {"action": "created", "event_id": event["id"], "link": link}


def event_schedule(event: dict) -> dict:
    """วัน/เวลาของนัด (รูปแบบ event ของ Google) -> รูปแบบเดียวกับงานของ MeetSync: {due_date, due_date_end, due_time, due_time_end}
    - เต็มวัน: end.date ของ Google ไม่รวมวันนั้น จึงลบออก 1 วัน; เป็นวันเดียวกับวันเริ่ม = ไม่มีวันสิ้นสุด
    - มีเวลา: แปลงเป็นเวลาไทยก่อนเสมอ (ผู้ใช้อาจแก้นัดจากอุปกรณ์ที่ตั้งเขตเวลาอื่น)"""
    start, end = event.get("start") or {}, event.get("end") or {}
    if "date" in start:
        first = datetime.date.fromisoformat(start["date"])
        last = datetime.date.fromisoformat(end["date"]) - datetime.timedelta(days=1) if end.get("date") else first
        return {"due_date": first.isoformat(), "due_date_end": last.isoformat() if last > first else None,
                "due_time": None, "due_time_end": None}

    def local(block):
        return datetime.datetime.fromisoformat(block["dateTime"]).astimezone(BANGKOK)

    s, e = local(start), local(end)
    return {"due_date": s.date().isoformat(), "due_date_end": e.date().isoformat() if e.date() > s.date() else None,
            "due_time": f"{s:%H:%M}", "due_time_end": f"{e:%H:%M}"}


def fetch_event(event_id: str, user_id: int, service=None) -> dict | None:
    """อ่านนัดจาก Google Calendar ของ user_id ตามรหัส — นัดไม่อยู่แล้ว (ผู้ใช้ลบเอง) หรืออยู่ในสถานะยกเลิก คืน None"""
    service = service or _calendar_service(user_id)
    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except Exception as e:  # noqa: BLE001
        if _http_status(e) in (404, 410):
            return None
        raise
    return None if event.get("status") == "cancelled" else event


def compare_with_google(item: dict, event: dict | None) -> dict:
    """เทียบงานของ MeetSync กับนัดใน Google: คืน {"status": same|changed|deleted, "mine": {...}, "remote": {...}}
    เทียบ 2 อย่าง: ชื่อนัด (description) และวัน/เวลา — ผู้รับผิดชอบไม่เทียบ (Calendar เก็บเป็นอีเมลผู้ได้รับเชิญ คนละชนิดกับชื่อผู้รับผิดชอบ)
    ฝั่ง MeetSync คำนวณวัน/เวลาเป็น "นัดที่เราเคยส่งไป" (build_event_body) แล้วแปลงเป็นรูปแบบเดียวกับ Google ก่อนเทียบ —
    ไม่งั้นนัดที่จบข้ามเที่ยงคืนหรือไม่ระบุเวลาจบ (เรากำหนดให้ยาว 1 ชม.) จะถูกมองว่าเปลี่ยนทั้งที่ไม่มีใครแก้
    ชื่อเทียบหลังรวมช่องว่างซ้ำ; ชื่อนัดว่างใน Calendar ไม่ถือว่าเปลี่ยน (คงชื่อเดิมของงาน)"""
    mine = {k: item.get(k) for k in ("due_date", "due_date_end", "due_time", "due_time_end")}
    mine = {k: (str(v)[:10] if k.startswith("due_date") and v else (str(v)[:5] if v else None)) for k, v in mine.items()}
    mine["description"] = " ".join((item.get("description") or "").split())
    if event is None:
        return {"status": "deleted", "mine": mine, "remote": None}
    remote = event_schedule(event)
    sent = event_schedule(build_event_body(item))
    remote["description"] = " ".join((event.get("summary") or "").split()) or mine["description"]
    same = {k: v for k, v in remote.items() if k != "description"} == sent and remote["description"] == mine["description"]
    return {"status": "same" if same else "changed", "mine": mine, "remote": remote}


def delete_event(event_id: str, user_id: int, service=None) -> bool:
    """ลบนัดออกจาก Google Calendar ของ user_id คืน True ถ้าลบจริง — นัดไม่อยู่แล้ว (ผู้ใช้ลบเอง) ไม่ถือว่าผิดพลาด คืน False"""
    service = service or _calendar_service(user_id)
    try:
        service.events().delete(calendarId="primary", eventId=event_id, sendUpdates="none").execute()
    except Exception as e:  # noqa: BLE001
        if _http_status(e) in (404, 410):
            return False
        raise
    return True
