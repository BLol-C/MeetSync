"""เติมวาระที่ต้องรู้ข้อมูลครั้งก่อน (วาระ 2 รับรองรายงาน, วาระ 3 เรื่องสืบเนื่อง, วาระ 4.1 เรื่องค้างพิจารณา) จากฐานข้อมูล

ระบบเติมจากรายงานที่ "อนุมัติแล้ว" ของการประชุมครั้งก่อนที่ผู้ใช้เลือก (meetings.previous_meeting_id) — ไม่ผ่าน AI จึงไม่มีทางแต่งข้อมูล
สิ่งที่ระบบรู้ไม่ได้ ให้คนตัดสินเองในแท็บรายงาน:
  - งานครั้งก่อนเสร็จหรือยัง (ฐานข้อมูลไม่เก็บสถานะ "ทำเสร็จ") จึงใส่ทุกงานเป็นรายการให้ตรวจ ลบรายการที่เสร็จแล้วออกเอง
  - เรื่องใดค้างพิจารณาจริง ใช้ประมาณจาก "วาระของครั้งก่อนที่ไม่มีมติ" ซึ่งอาจเป็นเรื่องแจ้งให้ทราบก็ได้
  - มติรับรองรายงาน (ที่ประชุมรับรองหรือขอแก้ไข) ให้คนกรอกเอง
"""

import db
from reports import report_data


def previous_report(meeting: dict) -> tuple[dict, dict] | None:
    """(การประชุมครั้งก่อน, รายงานที่อนุมัติแล้วของครั้งนั้น) หรือ None ถ้าไม่ได้เลือก/ยังไม่อนุมัติ"""
    prev_id = meeting.get("previous_meeting_id")
    if not prev_id:
        return None
    prev = db.get_meeting(prev_id)
    report = db.get_report(prev_id) if prev else None
    if not prev or not report or not report["approved"]:
        return None
    return prev, report


def _due_text(action: dict) -> str:
    parts = []
    if action.get("assignee"):
        parts.append(f"ผู้รับผิดชอบ: {action['assignee']}")
    if action.get("due_date"):
        when = str(action["due_date"])
        if action.get("due_time"):
            when += f" {action['due_time']}" + (f"-{action['due_time_end']}" if action.get("due_time_end") else "")
        parts.append(f"กำหนดส่ง: {when}")
    return " · ".join(parts)


def prefill_items(meeting: dict) -> list[dict]:
    """รายการวาระ (ในรูปแบบเดียวกับที่ AI สกัด + section) ที่เติมจากการประชุมครั้งก่อน เรียงตามหมวด"""
    found = previous_report(meeting)
    if not found:
        return []
    prev, report = found
    content = report["content"]
    when = prev.get("started_at") or prev.get("scheduled_at")
    no = (prev.get("meeting_no") or "").strip()
    title = "รับรองรายงานการประชุม" + (f" ครั้งที่ {no}" if no else "") + (f" เมื่อวันที่ {report_data.thai_date(when)}" if when else "")
    items = [{"section": "approve_prev", "title": title, "discussion": "", "resolution": None, "evidence": []}]
    for action in content.get("action_items") or []:
        items.append({"section": "followup", "title": action["description"], "discussion": _due_text(action),
                      "resolution": None, "evidence": []})
    for agenda in content.get("agenda") or []:
        if agenda.get("section") in ("consider_old", "consider_new") and not (agenda.get("resolution") or "").strip():
            items.append({"section": "consider_old", "title": agenda["title"], "discussion": agenda.get("discussion") or "",
                          "resolution": None, "evidence": []})
    return items
