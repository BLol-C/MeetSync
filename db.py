"""
ชั้นข้อมูลของ MeetSync (MySQL) — โครงสร้างตาม SA เดิม 6 ตาราง + agenda_items 1 ตาราง

  users                ผู้ใช้ระบบ (Google login) + token Google Calendar ของผู้ใช้
  meetings             การประชุม (หัวรายงาน: ชื่อเรื่อง สถานที่ ครั้งที่ หน่วยงาน) + สถานะ workflow เพียงที่เดียว
  speakers             "ผู้เข้าร่วมประชุม" หนึ่งคน = หนึ่งแถว (ชื่อ อีเมล บทบาท การเข้าร่วม) ทั้งคนที่ลงทะเบียนไว้ล่วงหน้า
                       และคนที่พบจากชื่อใน Meet — ไม่แยกเป็นสองตารางอีกต่อไป
  transcript_segments  คำพูดทีละช่วง (เก็บข้อความต้นฉบับไว้เมื่อมีการแก้)
  summaries            "รายงานการประชุม" หนึ่งฉบับต่อหนึ่งการประชุม (1:1 ตาม SA) approved_at ว่าง = ฉบับร่าง
  agenda_items         วาระ/มติของรายงาน
  action_items         งานที่ได้รับมอบหมายของรายงาน (ส่งเข้า Calendar จากตารางนี้)

ตั้งค่าการเชื่อมต่อผ่าน .env: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
ต้องสร้างฐานข้อมูลเปล่าไว้ก่อน (เช่น `CREATE DATABASE meetsync CHARACTER SET utf8mb4;`) — ตารางสร้างให้อัตโนมัติ
ไม่มีระบบอัปเกรดโครงสร้าง: ถ้า _SCHEMA เปลี่ยน ให้ลบฐานข้อมูลแล้วสร้างใหม่
"""

import contextlib
import json
import os
import re

import pymysql
from pymysql.constants import CLIENT
from pymysql.cursors import DictCursor

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "meetsync")

# ─────────────────────────────────────────────────────────────────────────────
# โครงสร้างปัจจุบัน (ใช้สร้างฐานข้อมูลเปล่า)
# ─────────────────────────────────────────────────────────────────────────────
_SCHEMA = """
CREATE TABLE users (
    user_id        INT AUTO_INCREMENT PRIMARY KEY,
    google_sub     VARCHAR(255) NOT NULL UNIQUE,
    email          VARCHAR(255) NOT NULL,
    name           VARCHAR(255) NULL,
    picture        VARCHAR(500) NULL,
    calendar_token LONGTEXT NULL,
    created_at     DATETIME NOT NULL,
    last_login_at  DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE meetings (
    meeting_id    INT AUTO_INCREMENT PRIMARY KEY,
    owner_user_id INT NULL,
    meet_url      VARCHAR(255) NOT NULL,
    title         VARCHAR(255) NULL,
    venue         VARCHAR(255) NULL,
    meeting_no    VARCHAR(50) NULL,
    org_name      VARCHAR(255) NULL,
    scheduled_at  DATETIME NULL,
    started_at    DATETIME NOT NULL,
    ended_at      DATETIME NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'recording',
    previous_meeting_id INT NULL,
    CONSTRAINT fk_meetings_owner FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    CONSTRAINT fk_meetings_previous FOREIGN KEY (previous_meeting_id) REFERENCES meetings(meeting_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE speakers (
    speaker_id    INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id    INT NOT NULL,
    display_name  VARCHAR(100) NOT NULL,
    meet_alias    VARCHAR(100) NULL,
    email         VARCHAR(255) NULL,
    role          VARCHAR(20) NOT NULL DEFAULT 'attendee',
    attendance    VARCHAR(10) NOT NULL DEFAULT 'invited',
    absence_reason VARCHAR(255) NULL,
    position      VARCHAR(100) NULL,
    source        VARCHAR(10) NOT NULL DEFAULT 'registered',
    UNIQUE KEY uq_speaker (meeting_id, display_name),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE transcript_segments (
    segment_id    INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id    INT NOT NULL,
    speaker_id    INT NOT NULL,
    sequence_no   INT NOT NULL,
    text          TEXT NOT NULL,
    original_text TEXT NULL,
    edited_at     DATETIME NULL,
    deleted       BOOLEAN NOT NULL DEFAULT FALSE,
    spoken_at     DATETIME NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    FOREIGN KEY (speaker_id) REFERENCES speakers(speaker_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE summaries (
    summary_id         INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id         INT NOT NULL UNIQUE,
    executive_summary  TEXT NOT NULL,
    other_matters      TEXT NULL,
    ai_snapshot        LONGTEXT NULL,
    model_used         VARCHAR(50) NULL,
    prompt_version     VARCHAR(20) NULL,
    generated_at       DATETIME NOT NULL,
    edited_at          DATETIME NULL,
    approved_by        INT NULL,
    approved_at        DATETIME NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    CONSTRAINT fk_summaries_approver FOREIGN KEY (approved_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE agenda_items (
    agenda_item_id INT AUTO_INCREMENT PRIMARY KEY,
    summary_id     INT NOT NULL,
    order_no       INT NOT NULL,
    section        VARCHAR(20) NOT NULL DEFAULT 'consider_new',
    title          VARCHAR(255) NOT NULL,
    discussion     TEXT NULL,
    resolution     TEXT NULL,
    evidence       TEXT NULL,
    INDEX idx_agenda_order (summary_id, order_no),
    FOREIGN KEY (summary_id) REFERENCES summaries(summary_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE action_items (
    action_item_id           INT AUTO_INCREMENT PRIMARY KEY,
    summary_id               INT NOT NULL,
    description              TEXT NOT NULL,
    assignee                 VARCHAR(100) NULL,
    due_date                 DATE NULL,
    due_time                 TIME NULL,
    due_time_end             TIME NULL,
    evidence                 TEXT NULL,
    calendar_synced          BOOLEAN NOT NULL DEFAULT FALSE,
    google_calendar_event_id VARCHAR(255) NULL,
    FOREIGN KEY (summary_id) REFERENCES summaries(summary_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

MEETING_STATUSES = ("scheduled", "recording", "transcript_review", "transcript_verified", "draft", "approved")
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    # workflow: scheduled -> recording -> transcript_review -> transcript_verified -> draft -> approved
    # approved = ล็อก ไม่ย้อนผ่าน set_meeting_status (ถ้าต้องแก้ ต้องผ่าน reopen_report ที่ยกเลิกการอนุมัติ แล้วอนุมัติใหม่หลังแก้)
    # transcript_review -> recording = กลับมาบันทึกต่อหลังบอทหลุดกลางประชุม
    "scheduled": {"recording"},
    "recording": {"transcript_review"},
    "transcript_review": {"transcript_verified", "recording"},
    "transcript_verified": {"transcript_review", "draft"},
    "draft": {"transcript_review", "draft", "approved"},
    "approved": set(),
}

ROLES = ("chair", "secretary", "attendee", "guest")  # ประธาน / เลขา / กรรมการ-สมาชิก (ผู้มาประชุม) / ผู้เข้าร่วม (ไม่ใช่กรรมการ)
UNIQUE_ROLES = ("chair", "secretary")               # แต่ละการประชุมมีได้คนเดียว
# หมวดของวาระในรายงานตามแบบฟอร์ม (วาระที่ 5 เรื่องอื่น ๆ เก็บเป็นข้อความ summaries.other_matters ไม่ใช่แถววาระ)
#   inform = วาระ 1 แจ้งให้ที่ประชุมทราบ · approve_prev = วาระ 2 รับรองรายงานครั้งก่อน · followup = วาระ 3 เรื่องสืบเนื่อง
#   consider_old = วาระ 4.1 เรื่องค้างพิจารณา · consider_new = วาระ 4.2 เรื่องพิจารณาใหม่ (ค่าเริ่มต้น — เรื่องที่ AI สรุป)
AGENDA_SECTIONS = ("inform", "approve_prev", "followup", "consider_old", "consider_new")
DEFAULT_AGENDA_SECTION = "consider_new"
ATTENDANCE = ("invited", "present", "absent")       # เชิญไว้ (ยังไม่ยืนยัน) / เข้าร่วม / ไม่มา


# ─────────────────────────────────────────────────────────────────────────────
# การเชื่อมต่อ / schema
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_time(value) -> str | None:
    """pymysql คืนคอลัมน์ TIME มาเป็น datetime.timedelta — แปลงเป็น 'HH:MM' ให้ JSON อ่านง่าย"""
    if value is None:
        return None
    if isinstance(value, str):
        return value[:5]
    total_minutes = int(value.total_seconds()) // 60
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        # rowcount ของ UPDATE นับ "แถวที่ตรงเงื่อนไข" ไม่ใช่ "แถวที่ค่าเปลี่ยนจริง" (สำคัญกับ draft -> draft)
        client_flag=CLIENT.FOUND_ROWS,
    )


@contextlib.contextmanager
def _tx():
    """ทรานแซกชัน: สำเร็จทั้งก้อนหรือย้อนกลับทั้งก้อน (ใช้กับงานที่แตะหลายตารางพร้อมกัน)"""
    conn = get_connection()
    try:
        conn.autocommit(False)
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,)
    )
    return cur.fetchone() is not None


def init_schema():
    """สร้างตารางทั้งหมดถ้าฐานข้อมูลยังว่าง — เรียกตอนแอปสตาร์ท (ทั้งหน้าเว็บและบริการบอท)

    ไม่มีการอัปเกรดโครงสร้างให้ฐานข้อมูลเก่า: ถ้าโครงสร้างใน _SCHEMA เปลี่ยน ให้ลบฐานข้อมูลแล้วสร้างใหม่
    (ถ้าเจอฐานข้อมูลที่โครงสร้างเก่ากว่า จะแจ้ง error ชัดเจนแทนที่จะพังตอนใช้งาน)
    หน้าเว็บกับบริการบอทอาจสตาร์ทพร้อมกัน จึงใช้ล็อกของ MySQL ให้สร้างทีละโปรเซส โปรเซสที่มาทีหลังรอจนเสร็จ
    """
    conn = get_connection()
    lock = f"meetsync_init_{DB_NAME}"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 120) AS ok", (lock,))
            if cur.fetchone()["ok"] != 1:
                raise RuntimeError("รออีกโปรเซสที่กำลังสร้างฐานข้อมูลนานเกินไป (เกิน 120 วินาที)")
        try:
            with conn.cursor() as cur:
                if _table_exists(cur, "meetings"):
                    _check_schema_is_current(cur)
                else:
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            cur.execute(statement)
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock,))
    finally:
        conn.close()


_NOT_COLUMNS = {"PRIMARY", "FOREIGN", "UNIQUE", "INDEX", "KEY", "CONSTRAINT"}


def _expected_columns() -> dict[str, set[str]]:
    """ตารางและคอลัมน์ที่ _SCHEMA กำหนด (อ่านจากข้อความ CREATE TABLE ของเราเอง)"""
    out: dict[str, set[str]] = {}
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\)", _SCHEMA, flags=re.S):
        out[table] = {m.group(1) for m in re.finditer(r"^\s{4}(\w+)", body, flags=re.M)
                      if m.group(1).upper() not in _NOT_COLUMNS}
    return out


def _check_schema_is_current(cur):
    cur.execute("SELECT TABLE_NAME AS t, COLUMN_NAME AS c FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()")
    have: dict[str, set[str]] = {}
    for row in cur.fetchall():
        have.setdefault(row["t"], set()).add(row["c"])
    missing = [f"{t}.{c}" if t in have else t
               for t, cols in _expected_columns().items() for c in sorted(cols - have.get(t, set()))]
    if missing:
        raise RuntimeError(
            f"ฐานข้อมูล {DB_NAME} มีโครงสร้างเก่ากว่าโค้ด (ขาด: {', '.join(dict.fromkeys(missing))}) — "
            f"ลบฐานข้อมูลนี้แล้วสร้างเปล่าใหม่ (DROP DATABASE {DB_NAME}; CREATE DATABASE {DB_NAME} CHARACTER SET utf8mb4;) "
            "ตารางจะถูกสร้างให้เองตอนเปิดแอป"
        )


def _json_list(value) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return [str(value)]
    return [str(x) for x in data] if isinstance(data, list) else []


def _dump_list(items) -> str | None:
    items = [str(i).strip() for i in (items or []) if str(i).strip()]
    return json.dumps(items, ensure_ascii=False) if items else None


# ─────────────────────────────────────────────────────────────────────────────
# ผู้ใช้
# ─────────────────────────────────────────────────────────────────────────────
def upsert_user(google_sub: str, email: str, name: str | None, picture: str | None) -> int:
    """สร้างผู้ใช้ใหม่ถ้ายังไม่มี หรืออัปเดต name/picture/last_login_at ถ้ามีแล้ว คืนค่า user_id"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (google_sub, email, name, picture, created_at, last_login_at)
                   VALUES (%s, %s, %s, %s, NOW(), NOW())
                   ON DUPLICATE KEY UPDATE email = %s, name = %s, picture = %s, last_login_at = NOW()""",
                (google_sub, email, name, picture, email, name, picture),
            )
            cur.execute("SELECT user_id FROM users WHERE google_sub = %s", (google_sub,))
            return cur.fetchone()["user_id"]
    finally:
        conn.close()


def get_user_name(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(name, email) AS n FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row["n"] if row else None
    finally:
        conn.close()


def save_calendar_token(user_id: int, token_json: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET calendar_token = %s WHERE user_id = %s", (token_json, user_id))
    finally:
        conn.close()


def get_calendar_token(user_id: int) -> str | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT calendar_token FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return row["calendar_token"] if row else None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# การประชุม
# ─────────────────────────────────────────────────────────────────────────────
_SETUP_EDITABLE = ("meet_url", "title", "venue", "scheduled_at", "meeting_no", "org_name", "previous_meeting_id")


def _clean(value):
    return (value.strip() or None) if isinstance(value, str) else value


def create_meeting_setup(owner_user_id: int, **fields) -> int:
    """สร้างการประชุมที่ตั้งค่าไว้ล่วงหน้า (status = scheduled) ก่อนสั่งบอทเข้าห้อง
    started_at เป็นแค่ค่าชั่วคราว (คอลัมน์บังคับ) จะถูกเขียนทับด้วยเวลาเริ่มบันทึกจริงใน begin_recording
    """
    unknown = set(fields) - set(_SETUP_EDITABLE)
    if unknown:
        raise ValueError(f"ฟิลด์ไม่ถูกต้อง: {', '.join(sorted(unknown))}")
    meet_url = (fields.get("meet_url") or "").strip()
    if not meet_url:
        raise ValueError("ต้องระบุลิงก์ Google Meet")
    fields["meet_url"] = meet_url
    cols = list(fields)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO meetings (owner_user_id, status, started_at, {", ".join(cols)})
                    VALUES (%s, 'scheduled', NOW(), {", ".join(["%s"] * len(cols))})""",
                (owner_user_id, *[_clean(fields[c]) for c in cols]),
            )
            return cur.lastrowid
    finally:
        conn.close()


def create_meeting(meet_url: str, owner_user_id: int) -> int:
    """สร้างการประชุมที่กำลังบันทึกทันที (ไม่ผ่านการตั้งค่า) — ใช้ในเทสต์/สคริปต์ ไม่ใช่เส้นทางปกติของหน้าเว็บ"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO meetings (meet_url, started_at, status, owner_user_id)
                   VALUES (%s, NOW(), 'recording', %s)""",
                (meet_url, owner_user_id),
            )
            return cur.lastrowid
    finally:
        conn.close()


def update_meeting_setup(meeting_id: int, **fields) -> None:
    unknown = set(fields) - set(_SETUP_EDITABLE)
    if unknown:
        raise ValueError(f"แก้ฟิลด์นี้ไม่ได้: {', '.join(sorted(unknown))}")
    if "meet_url" in fields and not (fields["meet_url"] or "").strip():
        raise ValueError("ต้องระบุลิงก์ Google Meet")
    if not fields:
        return
    sets = ", ".join(f"{col} = %s" for col in fields)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE meetings SET {sets} WHERE meeting_id = %s",
                (*[_clean(v) for v in fields.values()], meeting_id),
            )
    finally:
        conn.close()


def delete_scheduled_meeting(meeting_id: int) -> bool:
    """ลบการประชุมที่ตั้งค่าไว้แต่ยังไม่เคยเริ่มบอท (ลบรายชื่อผู้เข้าร่วมตามไปด้วย) — ประชุมที่เริ่มแล้วลบไม่ได้"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM meetings WHERE meeting_id = %s AND status = 'scheduled'", (meeting_id,))
            return cur.rowcount == 1
    finally:
        conn.close()


def begin_recording(meeting_id: int) -> bool:
    """เริ่ม (หรือกลับมาบันทึกต่อ) — ได้จาก scheduled / transcript_review / recording เท่านั้น
    เวลาเริ่มถูกตั้งใหม่เฉพาะตอนเริ่มครั้งแรก (scheduled) ถ้าบันทึกต่อจะคงเวลาเริ่มเดิมไว้
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE meetings
                   SET started_at = IF(status = 'scheduled', NOW(), started_at),
                       ended_at = NULL, status = 'recording'
                   WHERE meeting_id = %s AND status IN ('scheduled', 'transcript_review', 'recording')""",
                (meeting_id,),
            )
            return cur.rowcount == 1
    finally:
        conn.close()


def end_meeting(meeting_id: int):
    """บอทหยุดแล้ว: ปิดเวลาประชุมและส่งต่อให้ขั้นตรวจทาน transcript (เรียกซ้ำได้ ไม่ย้อนสถานะที่เดินหน้าไปแล้ว)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE meetings SET ended_at = NOW(), status = 'transcript_review'
                   WHERE meeting_id = %s AND status = 'recording'""",
                (meeting_id,),
            )
    finally:
        conn.close()


def set_meeting_status(meeting_id: int, new_status: str) -> bool:
    """เปลี่ยนสถานะตาม workflow — คืน True ถ้าเปลี่ยนได้, False ถ้าการย้ายนั้นไม่ได้รับอนุญาตจากสถานะปัจจุบัน

    ตรวจและเขียนใน UPDATE เดียว (WHERE status IN ...) จึงไม่มีช่องให้สองคำสั่งแข่งกันแล้วข้ามขั้น
    """
    if new_status not in MEETING_STATUSES:
        raise ValueError(f"สถานะไม่ถูกต้อง: {new_status}")
    allowed_from = [s for s, targets in _STATUS_TRANSITIONS.items() if new_status in targets]
    if not allowed_from:
        return False
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE meetings SET status = %s
                    WHERE meeting_id = %s AND status IN ({",".join(["%s"] * len(allowed_from))})""",
                (new_status, meeting_id, *allowed_from),
            )
            return cur.rowcount == 1
    finally:
        conn.close()


def get_meeting(meeting_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT meeting_id, meet_url, title, venue, scheduled_at, meeting_no, org_name,
                          started_at, ended_at, status, owner_user_id, previous_meeting_id
                   FROM meetings WHERE meeting_id = %s""",
                (meeting_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def is_meeting_manager(meeting: dict, user_id: int | None, email: str | None) -> bool:
    """เจ้าของการประชุม หรือผู้เข้าร่วมที่เป็นประธาน/เลขาและอีเมลตรงกับผู้ใช้ — จัดการประชุมนี้ได้
    (เทียบอีเมลแบบไม่สนตัวพิมพ์เล็ก/ใหญ่)
    """
    if user_id is not None and meeting.get("owner_user_id") == user_id:
        return True
    if not email:
        return False
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM speakers
                   WHERE meeting_id = %s AND role IN ('chair', 'secretary') AND LOWER(email) = LOWER(%s)
                   LIMIT 1""",
                (meeting["meeting_id"], email),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def list_meetings(owner_user_id: int, email: str | None = None, limit: int = 100) -> list[dict]:
    """รายการการประชุมล่าสุดที่ผู้ใช้จัดการได้ (เป็นเจ้าของ หรือเป็นประธาน/เลขาตามอีเมล)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.meeting_id, m.meet_url, m.title, m.meeting_no, m.org_name, m.started_at, m.ended_at, m.status, m.owner_user_id,
                          (SELECT COUNT(*) FROM transcript_segments t
                            WHERE t.meeting_id = m.meeting_id AND t.deleted = FALSE) AS segment_count
                   FROM meetings m
                   WHERE m.owner_user_id = %s
                      OR EXISTS (SELECT 1 FROM speakers p
                                 WHERE p.meeting_id = m.meeting_id AND p.role IN ('chair', 'secretary')
                                   AND %s IS NOT NULL AND LOWER(p.email) = LOWER(%s))
                   ORDER BY m.started_at DESC
                   LIMIT %s""",
                (owner_user_id, email, email, limit),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# ผู้เข้าร่วมประชุม (ตาราง speakers) + บทบาท
# ─────────────────────────────────────────────────────────────────────────────
_YOU_SUFFIX_RE = re.compile(r"\s*\((you|คุณ)\)\s*$", re.I)


def normalize_name(name: str) -> str:
    """ทำชื่อให้เทียบกันได้: ตัดช่องว่างซ้ำ, ไม่สนตัวพิมพ์เล็ก/ใหญ่, ตัดต่อท้าย "(You)"/"(คุณ)" ที่ Meet เติมให้ตัวเอง"""
    name = _YOU_SUFFIX_RE.sub("", name or "")
    return re.sub(r"\s+", " ", name).strip().casefold()


def _check_person_fields(role: str | None, attendance: str | None):
    if role is not None and role not in ROLES:
        raise ValueError(f"บทบาทไม่ถูกต้อง: {role} (ต้องเป็น {', '.join(ROLES)})")
    if attendance is not None and attendance not in ATTENDANCE:
        raise ValueError(f"สถานะการเข้าร่วมไม่ถูกต้อง: {attendance} (ต้องเป็น {', '.join(ATTENDANCE)})")


def _check_role_free(cur, meeting_id: int, role: str, except_speaker_id: int | None = None):
    """ประธาน/เลขามีได้คนเดียวต่อการประชุม — ถ้ามีคนถือบทบาทนี้อยู่แล้วให้แจ้งก่อนเขียนทับโดยไม่รู้ตัว"""
    if role not in UNIQUE_ROLES:
        return
    cur.execute(
        "SELECT speaker_id, display_name FROM speakers WHERE meeting_id = %s AND role = %s", (meeting_id, role)
    )
    for r in cur.fetchall():
        if r["speaker_id"] != except_speaker_id:
            label = "ประธาน" if role == "chair" else "เลขา"
            raise ValueError(f"การประชุมนี้มี{label}แล้ว ({r['display_name']}) — เปลี่ยนบทบาทของคนนั้นก่อน")


def _mark_present(cur, speaker_id: int):
    """มีเสียงพูดจริง = เข้าร่วม (เปลี่ยนเฉพาะที่ยังเป็น 'เชิญไว้' — ที่เลขาตั้งว่าไม่มา/เข้าร่วมแล้วไม่ถูกเปลี่ยนเอง)"""
    cur.execute(
        "UPDATE speakers SET attendance = 'present' WHERE speaker_id = %s AND attendance = 'invited'",
        (speaker_id,),
    )


def _find_speaker_for_name(cur, meeting_id: int, meet_name: str) -> int | None:
    """ชื่อที่ Meet แสดง -> ผู้เข้าร่วมที่ตรงกัน (ชื่อ/ชื่อเรียกใน Meet) — ตรงคนเดียวเท่านั้น ชื่อกำกวมไม่เดา"""
    cur.execute(
        "SELECT speaker_id FROM speakers WHERE meeting_id = %s AND (display_name = %s OR meet_alias = %s)",
        (meeting_id, meet_name, meet_name),
    )
    exact = cur.fetchall()
    if exact:
        return exact[0]["speaker_id"]
    target = normalize_name(meet_name)
    if not target:
        return None
    cur.execute("SELECT speaker_id, display_name, meet_alias FROM speakers WHERE meeting_id = %s", (meeting_id,))
    matches = [
        r["speaker_id"] for r in cur.fetchall()
        if normalize_name(r["display_name"]) == target or (r["meet_alias"] and normalize_name(r["meet_alias"]) == target)
    ]
    return matches[0] if len(matches) == 1 else None


def get_or_create_speaker(meeting_id: int, meet_name: str) -> int:
    """ผู้พูดจากชื่อที่ Meet แสดง: ตรงกับผู้เข้าร่วมที่ลงทะเบียนไว้ -> ใช้คนนั้น (ตัด "(You)" ให้) ไม่ตรง -> สร้างแถวใหม่
    (source = 'meet') ให้คนจับคู่/รวมกับผู้เข้าร่วมภายหลัง ผู้พูดที่มีเสียงจริงถูกนับว่าเข้าร่วม
    """
    meet_name = (meet_name or "").strip()[:100]    # คอลัมน์ยาว 100 ตัวอักษร
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            found = _find_speaker_for_name(cur, meeting_id, meet_name)
            if found is not None:
                _mark_present(cur, found)
                return found
            cur.execute(
                """INSERT INTO speakers (meeting_id, display_name, role, attendance, source)
                   VALUES (%s, %s, 'attendee', 'present', 'meet')""",
                (meeting_id, meet_name),
            )
            return cur.lastrowid
    finally:
        conn.close()


def add_speaker(
    meeting_id: int,
    display_name: str,
    email: str | None = None,
    role: str = "attendee",
    attendance: str = "invited",
    absence_reason: str | None = None,
    position: str | None = None,
) -> int:
    """ลงทะเบียนผู้เข้าร่วม (ก่อนหรือหลังประชุมก็ได้)"""
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("ต้องระบุชื่อผู้เข้าร่วม")
    _check_person_fields(role, attendance)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _check_role_free(cur, meeting_id, role)
            try:
                cur.execute(
                    """INSERT INTO speakers (meeting_id, display_name, email, role, attendance, absence_reason, position, source)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'registered')""",
                    (meeting_id, display_name, (email or "").strip() or None, role, attendance,
                     (absence_reason or "").strip() or None, (position or "").strip() or None),
                )
            except pymysql.err.IntegrityError:
                raise ValueError(f"มีผู้เข้าร่วมชื่อ \"{display_name}\" ในการประชุมนี้แล้ว")
            return cur.lastrowid
    finally:
        conn.close()


def mark_unconfirmed_absent(meeting_id: int) -> list[str]:
    """ผู้ที่ยังเป็น 'เชิญไว้' (ยังไม่ยืนยัน) หลังประชุมจบ = ไม่มา — เปลี่ยนสถานะในฐานข้อมูลให้ตรงกับที่รายงานแสดง
    (คนที่พูดในประชุมกลายเป็น 'เข้าร่วม' ไปแล้วตอนบันทึก) คืนชื่อที่ถูกเปลี่ยน เรียงตามลำดับ"""
    with _tx() as cur:
        cur.execute("SELECT display_name FROM speakers WHERE meeting_id = %s AND attendance = 'invited' ORDER BY speaker_id",
                    (meeting_id,))
        names = [r["display_name"] for r in cur.fetchall()]
        if names:
            cur.execute("UPDATE speakers SET attendance = 'absent' WHERE meeting_id = %s AND attendance = 'invited'",
                        (meeting_id,))
    return names


def get_speaker(speaker_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM speakers WHERE speaker_id = %s", (speaker_id,))
            return cur.fetchone()
    finally:
        conn.close()


def list_speakers(meeting_id: int) -> list[dict]:
    """ผู้เข้าร่วมทั้งหมดของการประชุม (ประธาน เลขา แล้วที่เหลือ) พร้อมจำนวนช่วงคำพูดที่ยังไม่ถูกลบ"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.*, COUNT(t.segment_id) AS segment_count
                   FROM speakers s
                   LEFT JOIN transcript_segments t ON t.speaker_id = s.speaker_id AND t.deleted = FALSE
                   WHERE s.meeting_id = %s
                   GROUP BY s.speaker_id
                   ORDER BY FIELD(s.role, 'chair', 'secretary', 'attendee', 'guest'), s.speaker_id""",
                (meeting_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


_SPEAKER_EDITABLE = ("display_name", "meet_alias", "email", "role", "attendance", "absence_reason", "position")


def update_speaker(speaker_id: int, **fields) -> None:
    unknown = set(fields) - set(_SPEAKER_EDITABLE)
    if unknown:
        raise ValueError(f"แก้ฟิลด์นี้ไม่ได้: {', '.join(sorted(unknown))}")
    if not fields:
        return
    if "display_name" in fields:
        fields["display_name"] = (fields["display_name"] or "").strip()
        if not fields["display_name"]:
            raise ValueError("ต้องระบุชื่อผู้เข้าร่วม")
    for key in ("email", "meet_alias", "absence_reason", "position"):
        if key in fields:
            fields[key] = (fields[key] or "").strip() or None
    _check_person_fields(fields.get("role"), fields.get("attendance"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT meeting_id FROM speakers WHERE speaker_id = %s", (speaker_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("ไม่พบผู้เข้าร่วมนี้")
            if "role" in fields:
                _check_role_free(cur, row["meeting_id"], fields["role"], except_speaker_id=speaker_id)
            sets = ", ".join(f"{col} = %s" for col in fields)
            try:
                cur.execute(f"UPDATE speakers SET {sets} WHERE speaker_id = %s", (*fields.values(), speaker_id))
            except pymysql.err.IntegrityError:
                raise ValueError("มีผู้เข้าร่วมชื่อนี้ในการประชุมนี้แล้ว")
    finally:
        conn.close()


def delete_speaker(speaker_id: int) -> None:
    """ลบผู้เข้าร่วมที่ไม่มีข้อความใน transcript เลย — ถ้ามีข้อความให้ใช้ merge_speakers รวมเข้ากับคนที่ถูกต้องแทน"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM transcript_segments WHERE speaker_id = %s", (speaker_id,))
            if cur.fetchone()["n"]:
                raise ValueError("ผู้เข้าร่วมนี้มีข้อความใน transcript — ใช้ \"รวมกับ\" คนที่ถูกต้องแทนการลบ")
            cur.execute("DELETE FROM speakers WHERE speaker_id = %s", (speaker_id,))
    finally:
        conn.close()


def merge_speakers(source_id: int, target_id: int) -> int:
    """รวมผู้พูดที่ Meet แสดงชื่อต่างจากที่ลงทะเบียน (เช่น "46 ธนาวีร์ บุญเกิด") เข้ากับผู้เข้าร่วมที่ถูกต้อง
    ข้อความทั้งหมดย้ายไปอยู่กับ target, ชื่อที่ Meet แสดงถูกจำเป็น meet_alias (ครั้งหน้าจับคู่อัตโนมัติ), แถว source ถูกลบ
    คืนจำนวนช่วงคำพูดที่ย้าย — ย้อนกลับไม่ได้ (ถ้ารวมผิดให้เพิ่มผู้เข้าร่วมใหม่แล้วย้ายข้อความรายช่วงในหน้าตรวจทาน)
    """
    if source_id == target_id:
        raise ValueError("เลือกคนเดียวกันไม่ได้")
    with _tx() as cur:
        cur.execute("SELECT * FROM speakers WHERE speaker_id IN (%s, %s) FOR UPDATE", (source_id, target_id))
        rows = {r["speaker_id"]: r for r in cur.fetchall()}
        if len(rows) != 2:
            raise ValueError("ไม่พบผู้เข้าร่วมที่เลือก")
        src, dst = rows[source_id], rows[target_id]
        if src["meeting_id"] != dst["meeting_id"]:
            raise ValueError("ผู้เข้าร่วมต้องอยู่ในการประชุมเดียวกัน")
        if src["role"] != "attendee":
            raise ValueError("ไม่รวมผู้ที่เป็นประธาน/เลขา — เปลี่ยนบทบาทก่อน")
        cur.execute("UPDATE transcript_segments SET speaker_id = %s WHERE speaker_id = %s", (target_id, source_id))
        moved = cur.rowcount
        alias = dst["meet_alias"] or src["display_name"]
        email = dst["email"] or src["email"]
        cur.execute("DELETE FROM speakers WHERE speaker_id = %s", (source_id,))
        cur.execute("UPDATE speakers SET meet_alias = %s, email = %s WHERE speaker_id = %s", (alias, email, target_id))
        if moved or src["attendance"] == "present":
            _mark_present(cur, target_id)
        return moved


# ─────────────────────────────────────────────────────────────────────────────
# Transcript
# ─────────────────────────────────────────────────────────────────────────────
def max_sequence_no(meeting_id: int) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) AS n FROM transcript_segments WHERE meeting_id = %s",
                (meeting_id,),
            )
            return cur.fetchone()["n"]
    finally:
        conn.close()


def insert_segment(meeting_id: int, speaker_id: int, sequence_no: int, text: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO transcript_segments
                   (meeting_id, speaker_id, sequence_no, text, spoken_at)
                   VALUES (%s, %s, %s, %s, NOW())""",
                (meeting_id, speaker_id, sequence_no, text),
            )
            return cur.lastrowid
    finally:
        conn.close()


def update_segment(segment_id: int, text: str):
    """แก้ไขข้อความของ segment เดิมระหว่างบันทึกสด (ต่อความยาวขึ้นตอนคนคนเดิมพูดต่อ แทนการ insert แถวใหม่)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE transcript_segments SET text = %s WHERE segment_id = %s",
                (text, segment_id),
            )
    finally:
        conn.close()


def get_transcript(meeting_id: int) -> list[dict]:
    """transcript ที่ใช้งานจริง (ไม่รวมช่วงที่ลบ) เรียงตามลำดับการพูด พร้อมชื่อผู้พูดที่ถูกต้อง"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.display_name, t.text, t.spoken_at
                   FROM transcript_segments t
                   JOIN speakers s ON s.speaker_id = t.speaker_id
                   WHERE t.meeting_id = %s AND t.deleted = FALSE
                   ORDER BY t.sequence_no""",
                (meeting_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def list_segments(meeting_id: int) -> list[dict]:
    """ทุกช่วงคำพูดของการประชุม (รวมที่ลบแล้ว เพื่อให้กู้คืนได้) เรียงตามลำดับพูด"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT t.segment_id, t.sequence_no, t.speaker_id, s.display_name,
                          t.text, t.original_text, t.edited_at, t.deleted, t.spoken_at
                   FROM transcript_segments t
                   JOIN speakers s ON s.speaker_id = t.speaker_id
                   WHERE t.meeting_id = %s
                   ORDER BY t.sequence_no""",
                (meeting_id,),
            )
            rows = list(cur.fetchall())
            for r in rows:
                r["deleted"] = bool(r["deleted"])
            return rows
    finally:
        conn.close()


def get_segment(segment_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT segment_id, meeting_id, speaker_id, text, original_text, deleted "
                "FROM transcript_segments WHERE segment_id = %s",
                (segment_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def edit_segment(
    segment_id: int, text: str | None = None, speaker_name: str | None = None, deleted: bool | None = None
) -> None:
    """แก้ช่วงคำพูด: ข้อความ / ผู้พูด (ระบุเป็นชื่อ ถ้ายังไม่มีผู้พูดชื่อนี้จะสร้างให้) / ลบ-กู้คืน
    ข้อความต้นฉบับถูกเก็บไว้ครั้งแรกที่มีการแก้ และไม่ถูกเขียนทับอีก
    """
    seg = get_segment(segment_id)
    if not seg:
        raise ValueError("ไม่พบช่วงคำพูดนี้")
    sets: list[str] = []
    args: list = []
    if text is not None:
        text = text.strip()
        if not text:
            raise ValueError("ข้อความห้ามว่าง (ถ้าต้องการเอาออกให้ลบช่วงคำพูดนี้)")
        if text != seg["text"]:
            sets += ["original_text = COALESCE(original_text, text)", "text = %s", "edited_at = NOW()"]
            args.append(text)
    if speaker_name is not None:
        speaker_name = speaker_name.strip()
        if not speaker_name:
            raise ValueError("ชื่อผู้พูดห้ามว่าง")
        speaker_id = get_or_create_speaker(seg["meeting_id"], speaker_name)
        if speaker_id != seg["speaker_id"]:
            sets += ["speaker_id = %s", "edited_at = NOW()"]
            args.append(speaker_id)
    if deleted is not None:
        sets.append("deleted = %s")
        args.append(bool(deleted))
    if not sets:
        return
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE transcript_segments SET {', '.join(sets)} WHERE segment_id = %s", (*args, segment_id)
            )
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# รายงานการประชุม (summaries + agenda_items + action_items)
#   เนื้อหาในโปรแกรมเป็น dict {"summary", "agenda": [...], "other_matters", "action_items": [...]}
#   ในฐานข้อมูลเก็บเป็นแถวตามตาราง ไม่มี JSON ซ้ำซ้อน ยกเว้น ai_snapshot (ภาพถ่ายผลดิบของ AI ไว้เทียบกับฉบับที่คนแก้)
# ─────────────────────────────────────────────────────────────────────────────
def _insert_report_rows(cur, summary_id: int, content: dict, carry: list[dict] | None = None):
    """เขียนวาระและงานของรายงาน (carry = แถวงานเดิมที่มีสถานะส่ง Calendar ไว้ ให้ย้ายสถานะไปยังรายการที่ไม่เปลี่ยน)"""
    for i, a in enumerate(content.get("agenda") or [], start=1):
        cur.execute(
            """INSERT INTO agenda_items (summary_id, order_no, section, title, discussion, resolution, evidence)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (summary_id, i, a.get("section") if a.get("section") in AGENDA_SECTIONS else DEFAULT_AGENDA_SECTION,
             (a.get("title") or "")[:255], a.get("discussion") or None,
             a.get("resolution") or None, _dump_list(a.get("evidence"))),
        )
    pool = list(carry or [])
    for it in content.get("action_items") or []:
        key = (it.get("description") or "", it.get("assignee") or None, str(it.get("due_date") or "") or None,
               it.get("due_time") or None, it.get("due_time_end") or None)
        synced, event_id = False, None
        for old in pool:   # งานที่ไม่ได้ถูกแก้ ยังเป็นรายการเดิมใน Calendar — ไม่ส่งซ้ำ
            if old["_key"] == key:
                synced, event_id = bool(old["calendar_synced"]), old["google_calendar_event_id"]
                pool.remove(old)
                break
        cur.execute(
            """INSERT INTO action_items
               (summary_id, description, assignee, due_date, due_time, due_time_end, evidence,
                calendar_synced, google_calendar_event_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (summary_id, it.get("description") or "", it.get("assignee") or None, it.get("due_date") or None,
             it.get("due_time") or None, it.get("due_time_end") or None, _dump_list(it.get("evidence")),
             synced, event_id),
        )


def _existing_action_rows(cur, summary_id: int) -> list[dict]:
    cur.execute("SELECT * FROM action_items WHERE summary_id = %s", (summary_id,))
    rows = list(cur.fetchall())
    for r in rows:
        r["_key"] = (r["description"], r["assignee"], str(r["due_date"]) if r["due_date"] else None,
                     _fmt_time(r["due_time"]), _fmt_time(r["due_time_end"]))
    return rows


def _report_row_to_dict(cur, row: dict) -> dict:
    sid = row["summary_id"]
    cur.execute("SELECT * FROM agenda_items WHERE summary_id = %s ORDER BY order_no, agenda_item_id", (sid,))
    agenda = [{"section": a["section"], "title": a["title"], "discussion": a["discussion"] or "",
               "resolution": a["resolution"], "evidence": _json_list(a["evidence"])} for a in cur.fetchall()]
    cur.execute("SELECT * FROM action_items WHERE summary_id = %s ORDER BY action_item_id", (sid,))
    actions = []
    for a in cur.fetchall():
        actions.append({
            "action_item_id": a["action_item_id"], "description": a["description"], "assignee": a["assignee"],
            "due_date": a["due_date"].isoformat() if a["due_date"] else None,
            "due_time": _fmt_time(a["due_time"]), "due_time_end": _fmt_time(a["due_time_end"]),
            "evidence": _json_list(a["evidence"]), "calendar_synced": bool(a["calendar_synced"]),
            "google_calendar_event_id": a["google_calendar_event_id"],
        })
    cur.execute("SELECT COALESCE(name, email) AS n FROM users WHERE user_id = %s", (row["approved_by"],))
    approver = cur.fetchone()
    return {
        "summary_id": sid, "meeting_id": row["meeting_id"],
        "approved": row["approved_at"] is not None, "approved_at": row["approved_at"],
        "approved_by": row["approved_by"], "approved_by_name": approver["n"] if approver else None,
        "model_used": row["model_used"], "prompt_version": row["prompt_version"],
        "generated_at": row["generated_at"], "edited_at": row["edited_at"],
        "ai_snapshot": json.loads(row["ai_snapshot"]) if row["ai_snapshot"] else None,
        "content": {"summary": row["executive_summary"], "agenda": agenda,
                    "other_matters": row["other_matters"], "action_items": actions},
    }


def get_report(meeting_id: int) -> dict | None:
    """รายงานของการประชุม (หนึ่งฉบับต่อหนึ่งการประชุม ตาม SA) — None ถ้ายังไม่มี; approved = อนุมัติแล้ว"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM summaries WHERE meeting_id = %s", (meeting_id,))
            row = cur.fetchone()
            return _report_row_to_dict(cur, row) if row else None
    finally:
        conn.close()


def save_report(
    meeting_id: int, content: dict, model_used: str | None = None, prompt_version: str | None = None,
    ai_snapshot: dict | None = None,
) -> dict:
    """บันทึกรายงานที่ AI ร่างใหม่ — ถ้ามีฉบับร่างอยู่แล้วจะถูกแทนที่ในแถวเดิม (สร้างสรุปใหม่ regenerate ตาม SA)
    รายงานที่อนุมัติแล้วเขียนทับไม่ได้ (ต้อง reopen_report ก่อน) คืน {summary_id}
    """
    snapshot = json.dumps(ai_snapshot if ai_snapshot is not None else content, ensure_ascii=False)
    with _tx() as cur:
        cur.execute("SELECT summary_id, approved_at FROM summaries WHERE meeting_id = %s FOR UPDATE", (meeting_id,))
        last = cur.fetchone()
        fields = (content.get("summary") or "", content.get("other_matters") or None, snapshot, model_used, prompt_version)
        if last and last["approved_at"] is not None:
            raise ValueError("รายงานอนุมัติแล้ว — ต้องยกเลิกการอนุมัติก่อนจึงจะสร้างใหม่ได้")
        if last:
            sid = last["summary_id"]
            cur.execute(
                """UPDATE summaries SET executive_summary = %s, other_matters = %s, ai_snapshot = %s,
                          model_used = %s, prompt_version = %s, generated_at = NOW(), edited_at = NULL
                   WHERE summary_id = %s""",
                (*fields, sid),
            )
            cur.execute("DELETE FROM agenda_items WHERE summary_id = %s", (sid,))
            cur.execute("DELETE FROM action_items WHERE summary_id = %s", (sid,))
        else:
            cur.execute(
                """INSERT INTO summaries
                   (meeting_id, executive_summary, other_matters, ai_snapshot, model_used, prompt_version, generated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                (meeting_id, *fields),
            )
            sid = cur.lastrowid
        _insert_report_rows(cur, sid, content)
        return {"summary_id": sid}


def update_report_content(summary_id: int, content: dict) -> bool:
    """บันทึกการแก้ไขของคน — ทำได้เฉพาะฉบับร่าง (อนุมัติแล้วล็อก) ฉบับ AI เดิมใน ai_snapshot ไม่ถูกแตะ"""
    with _tx() as cur:
        cur.execute("SELECT approved_at FROM summaries WHERE summary_id = %s FOR UPDATE", (summary_id,))
        row = cur.fetchone()
        if not row or row["approved_at"] is not None:
            return False
        carry = _existing_action_rows(cur, summary_id)
        cur.execute(
            "UPDATE summaries SET executive_summary = %s, other_matters = %s, edited_at = NOW() WHERE summary_id = %s",
            (content.get("summary") or "", content.get("other_matters") or None, summary_id),
        )
        cur.execute("DELETE FROM agenda_items WHERE summary_id = %s", (summary_id,))
        cur.execute("DELETE FROM action_items WHERE summary_id = %s", (summary_id,))
        _insert_report_rows(cur, summary_id, content, carry)
        return True


def approve_report(meeting_id: int, user_id: int | None) -> bool:
    """อนุมัติรายงานฉบับร่าง: รายงาน -> อนุมัติ และการประชุม -> approved (ทั้งคู่หรือไม่มีเลย)
    คืน False ถ้าการประชุมไม่ได้อยู่ในสถานะ draft หรือไม่มีฉบับร่างให้อนุมัติ
    """
    with _tx() as cur:
        cur.execute(
            "SELECT summary_id FROM summaries WHERE meeting_id = %s AND approved_at IS NULL FOR UPDATE",
            (meeting_id,),
        )
        row = cur.fetchone()
        if not row:
            return False
        cur.execute("UPDATE meetings SET status = 'approved' WHERE meeting_id = %s AND status = 'draft'", (meeting_id,))
        if cur.rowcount != 1:
            return False
        cur.execute(
            "UPDATE summaries SET approved_by = %s, approved_at = NOW() WHERE summary_id = %s",
            (user_id, row["summary_id"]),
        )
        return True


def reopen_report(meeting_id: int) -> bool:
    """ยกเลิกการอนุมัติเพื่อแก้รายงาน: รายงานกลับเป็นฉบับร่าง (ล้างผู้อนุมัติ/เวลาอนุมัติ) และการประชุมกลับเป็น draft
    เนื้อหาและสถานะส่ง Calendar ของแต่ละงานคงเดิม (งานที่ไม่ถูกแก้จึงไม่ถูกส่งซ้ำ) ต้องอนุมัติใหม่หลังแก้
    คืน False ถ้าการประชุมไม่ได้อยู่ในสถานะ approved
    """
    with _tx() as cur:
        cur.execute("UPDATE meetings SET status = 'draft' WHERE meeting_id = %s AND status = 'approved'", (meeting_id,))
        if cur.rowcount != 1:
            return False
        cur.execute(
            "UPDATE summaries SET approved_by = NULL, approved_at = NULL WHERE meeting_id = %s", (meeting_id,)
        )
        return True


# ─────────────────────────────────────────────────────────────────────────────
# งานที่ได้รับมอบหมาย (action_items) — ใช้กับ Calendar
# ─────────────────────────────────────────────────────────────────────────────
def get_action_item(action_item_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM action_items WHERE action_item_id = %s", (action_item_id,))
            item = cur.fetchone()
            if item:  # คอลัมน์ TIME ของ pymysql เป็น timedelta — แปลงเป็น "HH:MM"
                item["due_time"] = _fmt_time(item["due_time"])
                item["due_time_end"] = _fmt_time(item["due_time_end"])
            return item
    finally:
        conn.close()


def get_action_item_meeting(action_item_id: int) -> int | None:
    """meeting_id ของการประชุมที่ action item นี้อยู่ (None ถ้าไม่พบ)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.meeting_id FROM action_items a JOIN summaries s ON s.summary_id = a.summary_id
                   WHERE a.action_item_id = %s""",
                (action_item_id,),
            )
            row = cur.fetchone()
            return row["meeting_id"] if row else None
    finally:
        conn.close()


def mark_action_item_synced(action_item_id: int, event_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE action_items SET calendar_synced = TRUE, google_calendar_event_id = %s
                   WHERE action_item_id = %s""",
                (event_id, action_item_id),
            )
    finally:
        conn.close()
