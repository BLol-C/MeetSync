"""
ชั้นบริการ (use cases) ของ MeetSync — กฎสิทธิ์และลำดับขั้นของงานทั้งหมดอยู่ที่นี่ที่เดียว
หน้าเว็บ (Streamlit) เรียกฟังก์ชันเหล่านี้ ไม่แตะฐานข้อมูลเองโดยตรง จึงทดสอบได้โดยไม่ต้องเปิดหน้าเว็บ

ทุกฟังก์ชันรับ user = {"user_id", "email", "name"} และคืนข้อมูลธรรมดา (dict/list) หรือยก ServiceError
ที่มีข้อความภาษาไทยพร้อมแสดงให้ผู้ใช้เห็นได้ทันที
"""

import datetime

from integrations import calendar_auth
from integrations import calendar_sync
import db
from reports import pdf_report
from reports import report_data
from reports import summarizer

EDITABLE_STATUSES = ("scheduled", "recording", "transcript_review", "transcript_verified", "draft")


class ServiceError(Exception):
    """ข้อผิดพลาดที่แสดงให้ผู้ใช้เห็นได้ — kind: not_found / conflict / invalid / needs_confirmation / unavailable"""

    def __init__(self, message: str, kind: str = "invalid", extra: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.extra = extra or {}


def _value_error(e: ValueError) -> ServiceError:
    return ServiceError(str(e), "invalid")


def meeting_for(user: dict, meeting_id: int) -> dict:
    """การประชุมที่ผู้ใช้จัดการได้ (เจ้าของ หรือประธาน/เลขาตามอีเมล) — ไม่พบหรือไม่มีสิทธิ์ตอบเหมือนกัน
    (ไม่เปิดเผยว่ามีการประชุมนั้นอยู่จริง)
    """
    meeting = db.get_meeting(meeting_id)
    if not meeting or not db.is_meeting_manager(meeting, user.get("user_id"), user.get("email")):
        raise ServiceError("ไม่พบการประชุมนี้", "not_found")
    return meeting


def _require_status(meeting: dict, *allowed: str, hint: str = ""):
    if meeting["status"] not in allowed:
        raise ServiceError(
            f"ทำรายการนี้ไม่ได้ในสถานะ \"{meeting['status']}\"" + (f" — {hint}" if hint else ""), "conflict"
        )


# ── การประชุม ──

def check_meet_url(url: str) -> str:
    from bot.meet_engine import MEET_URL_RE   # import ตรงนี้เพื่อไม่ดึง Playwright เข้ามาตอนแค่ import service

    url = (url or "").strip()
    if not MEET_URL_RE.match(url):
        raise ServiceError("ลิงก์ไม่ถูกต้อง (ต้องเป็น https://meet.google.com/xxx-xxxx-xxx)", "invalid")
    return url


def default_title(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return f"การประชุม {report_data.thai_date(now)} {now:%H:%M} น."


def previous_meeting_choices(user: dict, exclude_id: int | None = None) -> list[dict]:
    """การประชุมที่อนุมัติรายงานแล้วและผู้ใช้จัดการได้ — ไว้เลือกเป็น "การประชุมครั้งก่อน" (ล่าสุดก่อน)"""
    return [m for m in db.list_meetings(user["user_id"], user.get("email"))
            if m["status"] == "approved" and m["meeting_id"] != exclude_id]


def _check_previous(user: dict, previous_meeting_id, own_id: int | None = None) -> int | None:
    if previous_meeting_id in (None, "", 0):
        return None
    try:
        pid = int(previous_meeting_id)
    except (TypeError, ValueError):
        raise ServiceError("การประชุมครั้งก่อนไม่ถูกต้อง", "invalid")
    if own_id is not None and pid == own_id:
        raise ServiceError("เลือกการประชุมนี้เป็นครั้งก่อนของตัวเองไม่ได้", "invalid")
    if not any(m["meeting_id"] == pid for m in previous_meeting_choices(user, own_id)):
        raise ServiceError("การประชุมครั้งก่อนต้องเป็นการประชุมที่อนุมัติรายงานแล้วและคุณจัดการได้", "invalid")
    return pid


def create_meeting(
    user: dict, *, meet_url: str, title: str | None = None, venue: str | None = None,
    meeting_no: str | None = None, org_name: str | None = None,
    scheduled_at: datetime.datetime | None = None, people: list[dict] | None = None,
    previous_meeting_id: int | None = None,
) -> int:
    """สร้างการประชุมพร้อมรายชื่อผู้เข้าร่วมและบทบาท (ยังไม่สั่งบอท) — ตรวจรายชื่อให้ผ่านทั้งหมดก่อนเขียน
    จะได้ไม่เหลือการประชุมครึ่งๆ กลางๆ ถ้ารายการท้ายผิด
    """
    url = check_meet_url(meet_url)
    previous_meeting_id = _check_previous(user, previous_meeting_id)
    people = [p for p in (people or []) if (p.get("display_name") or "").strip()]
    roles = [p.get("role", "attendee") for p in people]
    for r in db.UNIQUE_ROLES:
        if roles.count(r) > 1:
            raise ServiceError("ประธานและเลขามีได้อย่างละ 1 คนต่อการประชุม", "invalid")
    names = [p["display_name"].strip().casefold() for p in people]
    if len(names) != len(set(names)):
        raise ServiceError("มีชื่อผู้เข้าร่วมซ้ำกัน", "invalid")
    try:
        for p in people:
            db._check_person_fields(p.get("role", "attendee"), p.get("attendance", "invited"))
        meeting_id = db.create_meeting_setup(
            user["user_id"], meet_url=url, title=(title or "").strip() or default_title(), venue=venue,
            meeting_no=meeting_no, org_name=org_name, scheduled_at=scheduled_at,
            previous_meeting_id=previous_meeting_id,
        )
        for p in people:
            db.add_speaker(meeting_id, p["display_name"], p.get("email"), p.get("role", "attendee"),
                           p.get("attendance", "invited"), p.get("absence_reason"), p.get("position"))
    except ValueError as e:
        raise _value_error(e)
    return meeting_id


def list_meetings(user: dict) -> list[dict]:
    return db.list_meetings(user["user_id"], user.get("email"))


def meetings_with_url(user: dict, meet_url: str) -> list[dict]:
    """การประชุมของผู้ใช้ที่ใช้ลิงก์ Meet เดียวกัน (ไว้เตือนก่อนสร้างซ้ำ) — เทียบโดยตัดพารามิเตอร์ท้ายลิงก์"""
    base = (meet_url or "").strip().split("?")[0].lower()
    if not base:
        return []
    return [m for m in list_meetings(user) if (m["meet_url"] or "").split("?")[0].lower() == base]


def get_detail(user: dict, meeting_id: int) -> dict:
    meeting = meeting_for(user, meeting_id)
    people = db.list_speakers(meeting_id)
    header = report_data.build_header(meeting, people)
    return {
        "meeting": meeting,
        "people": people,
        "header": header,
        "header_warnings": report_data.header_warnings(header),
        "is_owner": meeting["owner_user_id"] == user.get("user_id"),
    }


def update_meeting(user: dict, meeting_id: int, **fields) -> None:
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, *EDITABLE_STATUSES, hint="รายงานที่อนุมัติแล้วแก้ข้อมูลการประชุมไม่ได้")
    if "meet_url" in fields:
        if meeting["status"] != "scheduled":
            raise ServiceError("เปลี่ยนลิงก์ได้เฉพาะก่อนเริ่มบอท", "conflict")
        fields["meet_url"] = check_meet_url(fields["meet_url"])
    if "previous_meeting_id" in fields:
        fields["previous_meeting_id"] = _check_previous(user, fields["previous_meeting_id"], meeting_id)
    try:
        db.update_meeting_setup(meeting_id, **fields)
    except ValueError as e:
        raise _value_error(e)


def delete_meeting(user: dict, meeting_id: int) -> None:
    meeting_for(user, meeting_id)
    if not db.delete_scheduled_meeting(meeting_id):
        raise ServiceError("ลบได้เฉพาะการประชุมที่ยังไม่เคยเริ่มบอท", "conflict")


# ── ผู้เข้าร่วม ──

def _person_meeting(user: dict, speaker_id: int) -> tuple[dict, dict]:
    person = db.get_speaker(speaker_id)
    if not person:
        raise ServiceError("ไม่พบผู้เข้าร่วมนี้", "not_found")
    meeting = meeting_for(user, person["meeting_id"])
    _require_status(meeting, *EDITABLE_STATUSES, hint="รายงานที่อนุมัติแล้วแก้รายชื่อไม่ได้")
    return person, meeting


def add_person(user: dict, meeting_id: int, display_name: str, email: str | None = None,
               role: str = "attendee", attendance: str = "invited", absence_reason: str | None = None,
               position: str | None = None) -> int:
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, *EDITABLE_STATUSES, hint="รายงานที่อนุมัติแล้วแก้รายชื่อไม่ได้")
    try:
        return db.add_speaker(meeting_id, display_name, email, role, attendance, absence_reason, position)
    except ValueError as e:
        raise _value_error(e)


def merge_people(user: dict, source_id: int, target_id: int) -> int:
    source, _ = _person_meeting(user, source_id)
    target = db.get_speaker(target_id)
    if not target or target["meeting_id"] != source["meeting_id"]:
        raise ServiceError("ผู้เข้าร่วมต้องอยู่ในการประชุมเดียวกัน", "invalid")
    try:
        return db.merge_speakers(source_id, target_id)
    except ValueError as e:
        raise _value_error(e)


_PERSON_FIELDS = ("display_name", "email", "role", "attendance", "absence_reason", "position")


def apply_people_table(user: dict, meeting_id: int, rows: list[dict]) -> dict:
    """บันทึกตารางผู้เข้าร่วมที่แก้ในหน้าเว็บทั้งก้อน: แถวที่มี speaker_id = แก้ไข, ไม่มี = เพิ่มใหม่,
    แถวเดิมที่หายไป = ลบ (ลบได้เฉพาะคนที่ไม่มีข้อความ) คืน {"updated", "added", "deleted", "errors": [...]}
    แต่ละแถวที่พลาดไม่ทำให้แถวอื่นล้มเหลว
    """
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, *EDITABLE_STATUSES, hint="รายงานที่อนุมัติแล้วแก้รายชื่อไม่ได้")
    current = {p["speaker_id"]: p for p in db.list_speakers(meeting_id)}
    result = {"updated": 0, "added": 0, "deleted": 0, "errors": []}

    def clean(value):
        return (value or "").strip() if isinstance(value, str) else (value or "")

    seen = set()
    updates = []
    adds = []
    for row in rows:
        sid = row.get("speaker_id")
        if sid is not None and sid == sid and int(sid) in current:      # sid == sid กัน NaN จาก pandas
            sid = int(sid)
            seen.add(sid)
            changes = {}
            for f in _PERSON_FIELDS:
                new = clean(row.get(f))
                if new != (current[sid][f] or ""):
                    changes[f] = (new or None) if f in ("email", "absence_reason", "position") else new
            if changes:
                updates.append((sid, changes))
        elif clean(row.get("display_name")):
            adds.append(row)

    # ลบก่อน: คนที่หายจากตาราง (เฉพาะที่ไม่มีข้อความ)
    for sid, person in current.items():
        if sid not in seen:
            try:
                db.delete_speaker(sid)
                result["deleted"] += 1
            except ValueError as e:
                result["errors"].append(f"{person['display_name']}: {e}")
    # ปลดบทบาทประธาน/เลขาที่กำลังย้ายออกก่อน แล้วค่อยตั้งบทบาทใหม่ (สลับประธานกันได้ในการบันทึกครั้งเดียว)
    updates.sort(key=lambda u: 0 if ("role" in u[1] and current[u[0]]["role"] in db.UNIQUE_ROLES) else 1)
    for sid, changes in updates:
        try:
            db.update_speaker(sid, **changes)
            result["updated"] += 1
        except ValueError as e:
            result["errors"].append(f"{current[sid]['display_name']}: {e}")
    for row in adds:
        try:
            db.add_speaker(meeting_id, row["display_name"], clean(row.get("email")) or None,
                           clean(row.get("role")) or "attendee", clean(row.get("attendance")) or "invited",
                           clean(row.get("absence_reason")) or None, clean(row.get("position")) or None)
            result["added"] += 1
        except ValueError as e:
            result["errors"].append(f"{clean(row.get('display_name'))}: {e}")
    return result


# ── transcript ──

def list_segments(user: dict, meeting_id: int) -> list[dict]:
    meeting_for(user, meeting_id)
    return db.list_segments(meeting_id)


def apply_segments_table(user: dict, meeting_id: int, rows: list[dict]) -> dict:
    """บันทึกตาราง transcript ที่แก้ในหน้าเว็บ: เทียบกับข้อมูลปัจจุบัน แก้เฉพาะช่วงที่เปลี่ยนจริง
    rows: {segment_id, display_name (ผู้พูด), text, deleted} คืน {"changed", "errors"} — ทำได้เฉพาะขั้นตรวจทาน
    """
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, "transcript_review",
                    hint="แก้ transcript ได้เฉพาะขั้นตรวจทาน (หลังบอทหยุดและก่อนกดยืนยัน)")
    current = {s["segment_id"]: s for s in db.list_segments(meeting_id)}
    result = {"changed": 0, "errors": []}
    for row in rows:
        sid = row.get("segment_id")
        if sid is None or sid != sid or int(sid) not in current:
            continue
        seg = current[int(sid)]
        kwargs = {}
        text = (row.get("text") or "").strip()
        if text != seg["text"]:
            kwargs["text"] = text
        speaker = (row.get("display_name") or "").strip()
        if speaker and speaker != seg["display_name"]:
            kwargs["speaker_name"] = speaker
        if bool(row.get("deleted")) != seg["deleted"]:
            kwargs["deleted"] = bool(row.get("deleted"))
        if not kwargs:
            continue
        try:
            db.edit_segment(int(sid), **kwargs)
            result["changed"] += 1
        except ValueError as e:
            result["errors"].append(f"ช่วงที่ {seg['sequence_no']}: {e}")
    return result


def verify_transcript(user: dict, meeting_id: int, bot_running: bool = False) -> dict:
    """ยืนยันว่าตรวจ transcript แล้ว — ปลดล็อกให้ AI สร้างรายงาน คืนรายชื่อที่ Meet แสดงแต่ยังไม่ได้รวมกับผู้เข้าร่วม"""
    meeting = meeting_for(user, meeting_id)
    if bot_running:
        raise ServiceError("บอทยังบันทึกการประชุมนี้อยู่ — หยุดบอทก่อน", "conflict")
    if not db.get_transcript(meeting_id):
        raise ServiceError("ไม่มีข้อความใน transcript ให้ยืนยัน", "conflict")
    if not db.set_meeting_status(meeting_id, "transcript_verified"):
        raise ServiceError(f"ยืนยัน transcript ไม่ได้ในสถานะ \"{meeting['status']}\"", "conflict")
    marked_absent = db.mark_unconfirmed_absent(meeting_id)   # ประชุมจบแล้ว: ที่ยังไม่ยืนยัน = ไม่มา (ตรงกับรายงาน)
    return {"unmapped": [p["display_name"] for p in db.list_speakers(meeting_id)
                         if p["source"] == "meet" and p["segment_count"]],
            "marked_absent": marked_absent}


def reopen_transcript(user: dict, meeting_id: int) -> None:
    """กลับไปแก้ transcript (รายงานฉบับร่างที่มีอยู่จะล้าสมัย ต้องให้ AI สร้างใหม่) — รายงานที่อนุมัติแล้วทำไม่ได้"""
    meeting = meeting_for(user, meeting_id)
    if not db.set_meeting_status(meeting_id, "transcript_review"):
        raise ServiceError(f"กลับไปแก้ transcript ไม่ได้ในสถานะ \"{meeting['status']}\"", "conflict")


TURN_GAP_S = 30   # ประโยคของคนเดียวกันที่ห่างกันไม่เกินนี้ ถือเป็นช่วงพูดเดียวกัน


def group_turns(segments: list[dict], gap_s: float = TURN_GAP_S) -> list[dict]:
    """จัดประโยคที่ต่อเนื่องของคนเดียวกันเป็น "ช่วงพูด" (ผู้พูด + เวลาเริ่ม–สุดท้าย + ข้อความรวม) เพื่อให้อ่านง่าย

    ใช้แสดงผล/ส่งออกเท่านั้น — ในฐานข้อมูลยังเก็บ 1 ประโยค = 1 แถว (บันทึกทันที ตรวจย้อนรายประโยคได้)
    ข้ามประโยคที่ลบแล้ว segments ต้องเรียงตามลำดับพูด (มี display_name, text, spoken_at, deleted)
    """
    turns: list[dict] = []
    for s in segments:
        if s.get("deleted"):
            continue
        cur = turns[-1] if turns else None
        if (cur and cur["display_name"] == s["display_name"]
                and (s["spoken_at"] - cur["end"]).total_seconds() <= gap_s):
            cur["text"] += " " + s["text"]
            cur["end"] = s["spoken_at"]
            cur["segment_ids"].append(s.get("segment_id"))
            cur["edited"] = cur["edited"] or bool(s.get("original_text"))
        else:
            turns.append({"display_name": s["display_name"], "start": s["spoken_at"], "end": s["spoken_at"],
                          "text": s["text"], "segment_ids": [s.get("segment_id")], "edited": bool(s.get("original_text"))})
    return turns


def format_turn_time(turn: dict) -> str:
    a, b = f"{turn['start']:%H:%M:%S}", f"{turn['end']:%H:%M:%S}"
    return a if a == b else f"{a}–{b}"


def transcript_text(user: dict, meeting_id: int) -> str:
    """ไฟล์ .txt สำหรับคนอ่าน/เก็บ: หนึ่งบรรทัดต่อหนึ่งช่วงพูด (ไม่รวมช่วงที่ลบ) — AI อ่านจากฐานข้อมูลรายประโยคโดยตรง ไม่ผ่านไฟล์นี้"""
    meeting_for(user, meeting_id)
    lines = [f"[{format_turn_time(t)}] {t['display_name']}: {t['text']}" for t in group_turns(db.list_segments(meeting_id))]
    return "\n".join(lines) + ("\n" if lines else "")


# ── รายงาน ──

def _validated(meeting: dict, content: dict) -> dict:
    """ตรวจเนื้อหารายงานกับรายชื่อ/transcript ปัจจุบัน (คำนวณคำเตือนสดทุกครั้งที่ดู/แก้/อนุมัติ)"""
    people = db.list_speakers(meeting["meeting_id"])
    texts = [r["text"] for r in db.get_transcript(meeting["meeting_id"])]
    when = meeting.get("started_at")
    return summarizer.validate_minutes(content, people, texts, when.date() if when else None)


def get_report_view(user: dict, meeting_id: int) -> dict:
    """ทุกอย่างที่หน้ารายงานต้องใช้: รายงาน (พร้อมคำเตือนที่คำนวณสด), ส่วนหัวรายงาน, สิทธิ์แก้"""
    meeting = meeting_for(user, meeting_id)
    report = db.get_report(meeting_id)
    people = db.list_speakers(meeting_id)
    header = report_data.build_header(meeting, people)
    if report:
        if report["approved"]:
            report["content"] = {**report["content"], "warnings": []}
        else:
            report["content"] = _validated(meeting, report["content"])
    return {
        "meeting": meeting,
        "report": report,
        "header": header,
        "header_warnings": report_data.header_warnings(header),
        "editable": meeting["status"] == "draft",
        "can_generate": meeting["status"] in ("transcript_verified", "draft"),
        "people": people,
    }


def generate_report(user: dict, meeting_id: int, generate=None) -> dict:
    """ให้ AI ร่างรายงาน (แทนที่ฉบับร่างที่ค้างอยู่) — ต้องยืนยัน transcript แล้ว"""
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, "transcript_verified", "draft",
                    hint="ตรวจทานและกด \"ยืนยัน transcript\" ก่อนให้ AI สร้างรายงาน")
    try:
        saved = summarizer.generate_for_meeting(meeting_id, generate)
    except (RuntimeError, ValueError) as e:
        raise ServiceError(str(e), "invalid")
    except Exception as e:  # noqa: BLE001 — ปัญหาฝั่ง Gemini (โหลดสูง/โควต้า/เครือข่าย)
        raise ServiceError(f"เรียก Gemini ไม่สำเร็จ: {e}", "unavailable")
    return {"summary_id": saved["summary_id"], "warnings": saved["content"]["warnings"]}


def keep_existing_report(user: dict, meeting_id: int) -> None:
    """แก้ transcript แล้วแต่ไม่ต้องการให้ AI ร่างใหม่: ใช้รายงานฉบับเดิม (รวมสิ่งที่คนแก้ไว้) ต่อ แล้วไปตรวจ/อนุมัติได้เลย
    คำเตือนคำนวณสดกับ transcript ล่าสุดอยู่แล้ว (เช่น หลักฐานที่อ้างอิงแต่ถูกแก้/ลบไป จะขึ้นเตือนก่อนอนุมัติ)"""
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, "transcript_verified", hint="ใช้รายงานเดิมต่อได้หลังยืนยัน transcript ที่แก้ใหม่เท่านั้น")
    if not db.get_report(meeting_id):
        raise ServiceError("ยังไม่มีรายงานให้ใช้ต่อ — ให้ AI ร่างรายงานก่อน", "conflict")
    if not db.set_meeting_status(meeting_id, "draft"):
        raise ServiceError("เปลี่ยนสถานะไม่ได้", "conflict")


def save_report_draft(user: dict, meeting_id: int, content: dict) -> dict:
    """บันทึกที่คนแก้ในรายงานฉบับร่าง (ตรวจและคำนวณคำเตือนใหม่ให้) — อนุมัติแล้วแก้ไม่ได้"""
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, "draft", hint="รายงานที่อนุมัติแล้วถูกล็อก ใช้ \"สร้างเวอร์ชันแก้ไข\" เพื่อแก้ต่อ")
    report = db.get_report(meeting_id)
    if not report or report["approved"]:
        raise ServiceError("ไม่มีรายงานฉบับร่างให้แก้", "conflict")
    allowed = {k: content.get(k) for k in ("summary", "agenda", "other_matters", "action_items")}
    try:
        cleaned = _validated(meeting, allowed)
    except (AttributeError, TypeError):
        raise ServiceError("รูปแบบเนื้อหารายงานไม่ถูกต้อง", "invalid")
    if not db.update_report_content(report["summary_id"], summarizer.for_storage(cleaned)):
        raise ServiceError("บันทึกไม่ได้ (รายงานถูกอนุมัติไปแล้ว)", "conflict")
    return cleaned


def approval_warnings(user: dict, meeting_id: int) -> list[dict]:
    """สิ่งที่ควรตรวจก่อนอนุมัติ (คำเตือนในเนื้อหา + คำเตือนส่วนหัวรายงาน)"""
    view = get_report_view(user, meeting_id)
    if not view["report"]:
        return []
    return view["report"]["content"]["warnings"] + view["header_warnings"]


def approve_report(user: dict, meeting_id: int, confirm_warnings: bool = False) -> None:
    """ประธาน/เลขา/เจ้าของอนุมัติรายงาน — ถ้ายังมีคำเตือนต้องยืนยัน (confirm_warnings) ว่าตรวจแล้ว"""
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, "draft", hint="ต้องมีรายงานฉบับร่างก่อนอนุมัติ")
    report = db.get_report(meeting_id)
    if not report or report["approved"]:
        raise ServiceError("ไม่มีรายงานฉบับร่างให้อนุมัติ", "conflict")
    cleaned = _validated(meeting, report["content"])
    warnings = cleaned["warnings"] + report_data.header_warnings(
        report_data.build_header(meeting, db.list_speakers(meeting_id)))
    if warnings and not confirm_warnings:
        raise ServiceError("ยังมีรายการที่ควรตรวจก่อนอนุมัติ", "needs_confirmation", {"warnings": warnings})
    db.update_report_content(report["summary_id"], summarizer.for_storage(cleaned))
    db.mark_unconfirmed_absent(meeting_id)   # คนที่เพิ่มหลังยืนยัน transcript ก็ไม่ให้ค้างเป็น "ยังไม่ยืนยัน" ในรายงานที่ล็อกแล้ว
    if not db.approve_report(meeting_id, user["user_id"]):
        raise ServiceError("อนุมัติไม่ได้ (สถานะเปลี่ยนไปแล้ว)", "conflict")


def reopen_report(user: dict, meeting_id: int) -> None:
    """รายงานที่อนุมัติแล้วต้องแก้: ยกเลิกการอนุมัติ กลับเป็นฉบับร่างเพื่อแก้ แล้วอนุมัติใหม่
    (เนื้อหาและสถานะส่ง Calendar ของงานที่ไม่ถูกแก้คงเดิม จึงไม่ส่งซ้ำ)"""
    meeting_for(user, meeting_id)
    if not db.reopen_report(meeting_id):
        raise ServiceError("ยกเลิกการอนุมัติได้เฉพาะรายงานที่อนุมัติแล้ว", "conflict")


def build_pdf(user: dict, meeting_id: int) -> tuple[bytes, str]:
    """PDF รายงานฉบับล่าสุด — ยังไม่อนุมัติมีลายน้ำ "ฉบับร่าง" ทุกหน้า คืน (ไบต์, ชื่อไฟล์)"""
    meeting = meeting_for(user, meeting_id)
    report = db.get_report(meeting_id)
    if not report:
        raise ServiceError("การประชุมนี้ยังไม่มีรายงาน", "not_found")
    people = db.list_speakers(meeting_id)
    approved = report["approved"]
    data = pdf_report.build_minutes_pdf(
        meeting, people, report["content"], approved=approved,
        approved_by=report["approved_by_name"] if approved else None, approved_at=report["approved_at"],
    )
    return data, f"meeting-minutes-{meeting_id}{'' if approved else '-draft'}.pdf"


# ── Google Calendar ──

def calendar_connected(user: dict) -> bool:
    return calendar_auth.is_connected(user["user_id"])


def sync_action_item(user: dict, action_item_id: int) -> str:
    """ส่งงานเข้า Calendar ของผู้ใช้ — ผู้จัดการประชุมเท่านั้น และรายงานต้องอนุมัติแล้ว"""
    meeting_id = db.get_action_item_meeting(action_item_id)
    if meeting_id is None:
        raise ServiceError("ไม่พบงานนี้", "not_found")
    meeting = meeting_for(user, meeting_id)
    if meeting["status"] != "approved":
        raise ServiceError("ส่งเข้า Calendar ได้หลังอนุมัติรายงานแล้วเท่านั้น", "conflict")
    try:
        return calendar_sync.sync_action_item(action_item_id, user["user_id"])
    except (RuntimeError, ValueError) as e:
        raise ServiceError(str(e), "invalid")
    except Exception as e:  # noqa: BLE001
        raise ServiceError(f"เขียนลง Calendar ไม่สำเร็จ: {e}", "unavailable")


def sync_all(user: dict, meeting_id: int) -> list[dict]:
    """ส่งงานทั้งหมดของรายงานที่อนุมัติแล้วเข้า Calendar — รายการที่ส่งไม่ได้ (เช่น ไม่มีวัน) ไม่ล้มทั้งชุด"""
    meeting = meeting_for(user, meeting_id)
    _require_status(meeting, "approved", hint="ส่งเข้า Calendar ได้หลังอนุมัติรายงานแล้วเท่านั้น")
    report = db.get_report(meeting_id)
    results = []
    for it in (report["content"]["action_items"] if report else []):
        entry = {"action_item_id": it["action_item_id"], "description": it["description"]}
        if it["calendar_synced"]:
            results.append({**entry, "ok": True, "already": True})
            continue
        if not it.get("due_date"):   # ไม่มีวันที่ = ไม่มีอะไรให้ลงปฏิทิน ข้ามเฉย ๆ ไม่นับเป็นความล้มเหลว
            continue
        try:
            sync_action_item(user, it["action_item_id"])
            results.append({**entry, "ok": True})
        except ServiceError as e:
            results.append({**entry, "ok": False, "error": str(e)})
    return results
