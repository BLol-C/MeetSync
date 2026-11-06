"""วาระที่ต้องรู้ข้อมูลครั้งก่อน (วาระ 2 รับรองรายงาน, วาระ 3 เรื่องสืบเนื่อง, วาระ 4.1 เรื่องค้างพิจารณา)

ระบบสร้าง "โครง" ของวาระเหล่านี้จากรายงานที่ "อนุมัติแล้ว" ของการประชุมครั้งก่อนที่ผู้ใช้เลือก (meetings.previous_meeting_id)
ข้อเท็จจริงของครั้งก่อน (ครั้งที่ วันที่ เวลา สถานที่ งานที่มอบหมาย กำหนดส่ง) มาจากฐานข้อมูลตรงๆ ไม่ผ่าน AI จึงไม่มีทางแต่งข้อมูล
จากนั้น AI อ่าน transcript ของการประชุมครั้งนี้ แล้วเติม "ความคืบหน้า" และ "มติ" ให้เฉพาะรายการที่ถูกพูดถึงจริง พร้อมข้อความอ้างอิง
(apply_carried) — AI เพิ่มหรือลบรายการเองไม่ได้ และถ้าไม่มีหลักฐานจะไม่เติม คนแก้ไขได้ทุกช่องในแท็บรายงาน
สิ่งที่ระบบรู้ไม่ได้ ให้คนตัดสินเองในแท็บรายงาน:
  - งานครั้งก่อนเสร็จหรือยัง (ฐานข้อมูลไม่เก็บสถานะ "ทำเสร็จ") จึงใส่ทุกงานเป็นรายการให้ตรวจ ลบรายการที่เสร็จแล้วหรือไม่ได้พูดถึงออกเอง
  - เรื่องใดค้างพิจารณาจริง ใช้ประมาณจาก "วาระของครั้งก่อนที่ไม่มีมติ" ซึ่งอาจเป็นเรื่องแจ้งให้ทราบก็ได้
"""

import db
from reports import report_data

REF_LABEL = {"approve_prev": "รับรองรายงานครั้งก่อน", "followup": "งานที่ครั้งก่อนมอบหมาย", "consider_old": "เรื่องค้างพิจารณาจากครั้งก่อน"}


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
        when = str(action["due_date"]) + (f" ถึง {action['due_date_end']}" if action.get("due_date_end") else "")
        if action.get("due_time"):
            when += f" {action['due_time']}" + (f"-{action['due_time_end']}" if action.get("due_time_end") else "")
        parts.append(f"กำหนดส่ง: {when}")
    return " · ".join(parts)


def _when(prev: dict) -> str:
    """"เมื่อวันพุธที่ 14 กุมภาพันธ์ 2567 เวลา 19.00-20.30 น." จากข้อมูลจริงของการประชุมครั้งก่อน (ส่วนที่ไม่มีข้อมูลข้ามไป)"""
    day = prev.get("started_at") or prev.get("scheduled_at")
    if not day:
        return ""
    text = f"เมื่อวัน{report_data.thai_weekday(day)}ที่ {report_data.thai_date(day)}"
    start, end = prev.get("started_at"), prev.get("ended_at")
    if start and end:
        text += f" เวลา {start:%H:%M}-{end:%H:%M} น."
    elif start:
        text += f" เวลา {start:%H:%M} น."
    return text


def _approve_text(prev: dict) -> str:
    no = (prev.get("meeting_no") or "").strip()
    org = (prev.get("org_name") or "").strip()
    venue = (prev.get("venue") or "").strip()
    text = "ประธานขอให้ที่ประชุมพิจารณารายงานการประชุม" + (f" {org}" if org else "") + (f" ครั้งที่ {no}" if no else "")
    when = _when(prev)
    if when:
        text += " " + when
    if venue:
        text += f" ณ {venue}"
    return text + " ประธานจึงเสนอให้ที่ประชุมพิจารณา"


def _followup_text(prev: dict, action: dict) -> str:
    no = (prev.get("meeting_no") or "").strip()
    head = "ประธานแจ้งว่า จากการประชุม" + (f"ครั้งที่ {no}" if no else "ครั้งก่อน")
    when = _when(prev)
    if when:
        head += " " + when
    who = (action.get("assignee") or "").strip()
    text = head + (f" ได้มอบหมายให้ {who} ดำเนินการเรื่องนี้" if who else " ได้มอบหมายให้ดำเนินการเรื่องนี้")
    due = _due_text({k: v for k, v in action.items() if k != "assignee"})
    return text + (f" ({due})" if due else "")


def prefill_items(meeting: dict) -> list[dict]:
    """รายการวาระ (รูปแบบเดียวกับที่ AI สกัด + section) ที่ระบบสร้างโครงจากการประชุมครั้งก่อน เรียงตามหมวด
    แต่ละรายการมี "ref" (R1, R2, …) ไว้ให้ AI อ้างตอนเติมเนื้อหา ช่องมติว่างไว้เสมอจนกว่าจะมีหลักฐานจากการประชุมครั้งนี้"""
    found = previous_report(meeting)
    if not found:
        return []
    prev, report = found
    content = report["content"]
    when = prev.get("started_at") or prev.get("scheduled_at")
    no = (prev.get("meeting_no") or "").strip()
    title = "รับรองรายงานการประชุม" + (f" ครั้งที่ {no}" if no else "") + (f" เมื่อวันที่ {report_data.thai_date(when)}" if when else "")
    items = [{"section": "approve_prev", "title": title, "discussion": _approve_text(prev), "resolution": None, "evidence": []}]
    for action in content.get("action_items") or []:
        items.append({"section": "followup", "title": action["description"], "discussion": _followup_text(prev, action),
                      "resolution": None, "evidence": []})
    for agenda in content.get("agenda") or []:
        if agenda.get("section") in ("consider_old", "consider_new") and not (agenda.get("resolution") or "").strip():
            items.append({"section": "consider_old", "title": agenda["title"], "discussion": agenda.get("discussion") or "",
                          "resolution": None, "evidence": []})
    for n, it in enumerate(items, start=1):
        it["ref"] = f"R{n}"
    return items


def prompt_list(items: list[dict]) -> str:
    """รายการที่ให้ AI ดู (อ้างด้วย ref) — ว่าง = ไม่มีครั้งก่อน"""
    lines = []
    for it in items:
        extra = ""
        if it["section"] == "consider_old" and it.get("discussion"):
            extra = f" — {it['discussion'][:300]}"
        elif it["section"] == "followup":
            extra = f" — {it['discussion']}"
        lines.append(f"- {it['ref']} [{REF_LABEL[it['section']]}] {it['title']}{extra}")
    return "\n".join(lines)


def apply_carried(items: list[dict], carried: list[dict]) -> list[dict]:
    """เติมสิ่งที่ AI ตอบ (ความคืบหน้า มติ หลักฐาน) เข้าโครง — เติมเฉพาะรายการที่มีอยู่และมีหลักฐานเท่านั้น
    AI ตอบรายการที่ไม่มีในโครง หรือมีเนื้อหา/มติแต่ไม่มีหลักฐาน = ข้าม (ปล่อยว่างให้คนกรอก)"""
    by_ref = {it["ref"]: it for it in items}
    done = set()
    for c in carried or []:
        item = by_ref.get(str(c.get("ref") or "").strip())
        if item is None or item["ref"] in done:
            continue
        progress = (c.get("progress") or "").strip()
        resolution = (c.get("resolution") or "").strip()
        evidence = [e.strip() for e in (c.get("evidence") or []) if isinstance(e, str) and e.strip()]
        if not (progress or resolution) or not evidence:
            continue
        done.add(item["ref"])
        if progress:
            item["discussion"] = (item["discussion"] + " " + progress).strip()
        item["resolution"] = resolution or None
        item["evidence"] = evidence
    return items
