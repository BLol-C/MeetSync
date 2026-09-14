"""เขียน action item ที่ Gemini แยกออกมา กลับไปเป็น event ใน Google Calendar ของผู้ใช้"""

import datetime

from googleapiclient.discovery import build

import calendar_auth
import db


def _hhmm(value: datetime.timedelta) -> tuple[int, int]:
    total_minutes = int(value.total_seconds()) // 60
    return divmod(total_minutes, 60)


def sync_action_item(action_item_id: int) -> str:
    """สร้าง Calendar event จาก action item นี้ บันทึก event id กลับ DB คืนค่า event id"""
    creds = calendar_auth.get_credentials()
    if creds is None:
        raise RuntimeError("ยังไม่เชื่อมต่อ Google Calendar")

    item = db.get_action_item(action_item_id)
    if item is None:
        raise ValueError("ไม่พบ action item นี้")
    if item["calendar_synced"]:
        return item["google_calendar_event_id"]

    date_str = str(item["due_date"]) if item["due_date"] else (
        datetime.date.today() + datetime.timedelta(days=1)
    ).isoformat()

    detail_lines = []
    if item["assignee"]:
        detail_lines.append(f"ผู้รับผิดชอบ: {item['assignee']}")

    if item["due_time"]:  # pymysql คืนคอลัมน์ TIME มาเป็น datetime.timedelta
        start_hour, start_minute = _hhmm(item["due_time"])
        if item["due_time_end"]:
            end_hour, end_minute = _hhmm(item["due_time_end"])
            detail_lines.append(
                f"กำหนด: {date_str} เวลา {start_hour:02d}:{start_minute:02d} - {end_hour:02d}:{end_minute:02d} น."
            )
        else:
            end_hour, end_minute = (start_hour + 1) % 24, start_minute
            detail_lines.append(f"กำหนด: {date_str} เวลา {start_hour:02d}:{start_minute:02d} น.")
        start = {"dateTime": f"{date_str}T{start_hour:02d}:{start_minute:02d}:00", "timeZone": "Asia/Bangkok"}
        end = {"dateTime": f"{date_str}T{end_hour:02d}:{end_minute:02d}:00", "timeZone": "Asia/Bangkok"}
    else:
        detail_lines.append(f"กำหนด: {date_str}")
        start = {"date": date_str}
        end = {"date": date_str}

    body = {
        "summary": item["description"],
        "description": "\n".join(detail_lines) if detail_lines else "สร้างจาก MeetSync",
        "start": start,
        "end": end,
    }

    service = build("calendar", "v3", credentials=creds)
    event = service.events().insert(calendarId="primary", body=body).execute()
    db.mark_action_item_synced(action_item_id, event["id"])
    return event["id"]
