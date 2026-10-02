"""
ประกอบ "ส่วนหัวรายงาน" จากข้อมูลในระบบ (ไม่ให้ AI สร้าง): วันที่ เวลา สถานที่ ประธาน เลขา ผู้มา/ไม่มาประชุม

ใช้ร่วมกันระหว่าง API (แสดงตัวอย่างในหน้าเว็บ) และ PDF
"""

import datetime

_THAI_MONTHS = [
    "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def thai_date(d: datetime.date | datetime.datetime | None) -> str:
    """วันที่แบบไทย พ.ศ. เช่น 1 ตุลาคม 2569"""
    if d is None:
        return "-"
    return f"{d.day} {_THAI_MONTHS[d.month - 1]} {d.year + 543}"


def thai_datetime(d: datetime.datetime | None) -> str:
    if d is None:
        return "-"
    return f"{thai_date(d)} เวลา {d:%H:%M} น."


def _hhmm(d: datetime.datetime | None) -> str | None:
    return f"{d:%H:%M}" if d else None


def header_warnings(header: dict) -> list[dict]:
    """ข้อควรตรวจก่อนอนุมัติที่เกี่ยวกับส่วนหัวรายงาน (ไม่ได้มาจาก AI)"""
    out = []

    def warn(code: str, message: str):
        out.append({"code": code, "path": "header", "message": message})

    if not header["chair"]:
        warn("no_chair", "ยังไม่ได้กำหนดประธานการประชุม")
    if not header["secretary"]:
        warn("no_secretary", "ยังไม่ได้กำหนดเลขานุการ (ผู้จดรายงาน)")
    if not header["attendees"]:
        warn("no_attendees", "ยังไม่มีผู้มาประชุม — กำหนดการเข้าร่วมของผู้เข้าร่วมแต่ละคนก่อน")
    if header["unconfirmed_count"]:
        warn("unconfirmed_attendance",
             f"มีผู้เข้าร่วม {header['unconfirmed_count']} คนที่ยังไม่ยืนยันการเข้าร่วม — จะแสดงเป็น \"ผู้ไม่มาประชุม\"")
    if header["unmapped"]:
        warn("unmapped_speaker",
             "มีชื่อที่พบใน Meet แต่ไม่ได้อยู่ในรายชื่อที่ลงทะเบียน: " + ", ".join(header["unmapped"])
             + " — ถ้าเป็นคนเดียวกับผู้เข้าร่วมที่ลงทะเบียนไว้ให้ \"รวมชื่อ\" ในแท็บ Transcript")
    return out


_ROLE_ORDER = {"chair": 0, "secretary": 1, "attendee": 2}


def _sorted(people: list[dict]) -> list[dict]:
    return sorted(people, key=lambda p: (_ROLE_ORDER.get(p["role"], 9), p["speaker_id"]))


def build_header(meeting: dict, participants: list[dict]) -> dict:
    """ข้อมูลส่วนหัวรายงาน

    attendees = ผู้ที่ยืนยันว่าเข้าร่วม (present: มีเสียงพูดในประชุมหรือเลขากำหนดเอง)
    absent    = ผู้ที่ไม่มา (absent) รวมผู้ที่ยังเป็น invited คือไม่มีหลักฐานว่าเข้าร่วม
                (unconfirmed แยกบอกจำนวนไว้ให้หน้าเว็บเตือนให้เลขายืนยันก่อนอนุมัติ)
    """
    when = meeting.get("started_at") or meeting.get("scheduled_at")
    chair = next((p for p in participants if p["role"] == "chair"), None)
    secretary = next((p for p in participants if p["role"] == "secretary"), None)
    present = _sorted([p for p in participants if p["attendance"] == "present"])
    absent = _sorted([p for p in participants if p["attendance"] in ("absent", "invited")])
    return {
        "title": meeting.get("title") or "การประชุม",
        "org_name": meeting.get("org_name"),
        "meeting_no": meeting.get("meeting_no"),
        "date_text": thai_date(when),
        "start_time": _hhmm(meeting.get("started_at")),
        "end_time": _hhmm(meeting.get("ended_at")),
        "venue": meeting.get("venue"),
        "chair": chair["display_name"] if chair else None,
        "secretary": secretary["display_name"] if secretary else None,
        "attendees": [{"name": p["display_name"], "role": p["role"]} for p in present],
        "absent": [
            {"name": p["display_name"], "role": p["role"], "unconfirmed": p["attendance"] == "invited"}
            for p in absent
        ],
        "unconfirmed_count": sum(1 for p in participants if p["attendance"] == "invited"),
        # ชื่อที่พบจาก Meet (ไม่ได้ลงทะเบียน) และมีข้อความอยู่จริง — อาจเป็นคนเดียวกับผู้ที่ลงทะเบียนไว้แต่ชื่อไม่ตรง
        "unmapped": [p["display_name"] for p in participants
                     if p.get("source") == "meet" and p.get("segment_count", 0) > 0],
    }
