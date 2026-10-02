"""
บันทึกการประชุม/ผู้พูด/transcript ลง MySQL ให้ถาวร (แทนที่จะอยู่แค่ใน RAM)

ตั้งค่าการเชื่อมต่อผ่าน .env: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
ต้องสร้างฐานข้อมูลเปล่าไว้ก่อน (เช่น `CREATE DATABASE meetsync;`) — ตารางข้างในสร้างให้
อัตโนมัติตอนเรียก init_schema()

ตาราง summaries/action_items สร้าง schema ไว้รอสำหรับขั้น AI Summarization ถัดไป ยังไม่มี
ฟังก์ชัน insert ให้ในไฟล์นี้
"""

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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INT AUTO_INCREMENT PRIMARY KEY,
    google_sub    VARCHAR(255) NOT NULL UNIQUE,
    email         VARCHAR(255) NOT NULL,
    name          VARCHAR(255) NULL,
    picture       VARCHAR(500) NULL,
    created_at    DATETIME NOT NULL,
    last_login_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS meetings (
    meeting_id  INT AUTO_INCREMENT PRIMARY KEY,
    meet_url    VARCHAR(255) NOT NULL,
    title       VARCHAR(255) NULL,
    started_at  DATETIME NOT NULL,
    ended_at    DATETIME NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'recording'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS speakers (
    speaker_id    INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id    INT NOT NULL,
    display_name  VARCHAR(100) NOT NULL,
    UNIQUE KEY uq_speaker (meeting_id, display_name),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS transcript_segments (
    segment_id   INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id   INT NOT NULL,
    speaker_id   INT NOT NULL,
    sequence_no  INT NOT NULL,
    text         TEXT NOT NULL,
    spoken_at    DATETIME NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    FOREIGN KEY (speaker_id) REFERENCES speakers(speaker_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS summaries (
    summary_id         INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id         INT NOT NULL UNIQUE,
    executive_summary  TEXT NOT NULL,
    model_used         VARCHAR(50) NULL,
    generated_at       DATETIME NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS action_items (
    action_item_id           INT AUTO_INCREMENT PRIMARY KEY,
    summary_id               INT NOT NULL,
    description              TEXT NOT NULL,
    assignee                 VARCHAR(100) NULL,
    due_date                 DATE NULL,
    due_time                 TIME NULL,
    due_time_end              TIME NULL,
    calendar_synced          BOOLEAN NOT NULL DEFAULT FALSE,
    google_calendar_event_id VARCHAR(255) NULL,
    FOREIGN KEY (summary_id) REFERENCES summaries(summary_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS participants (
    participant_id INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id     INT NOT NULL,
    display_name   VARCHAR(100) NOT NULL,
    email          VARCHAR(255) NULL,
    role           VARCHAR(20) NOT NULL DEFAULT 'attendee',
    attendance     VARCHAR(10) NOT NULL DEFAULT 'invited',
    user_id        INT NULL,
    created_at     DATETIME NOT NULL,
    INDEX idx_participants_meeting (meeting_id),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS minutes (
    minutes_id     INT AUTO_INCREMENT PRIMARY KEY,
    meeting_id     INT NOT NULL,
    version        INT NOT NULL,
    status         VARCHAR(10) NOT NULL DEFAULT 'draft',
    content        LONGTEXT NOT NULL,
    model_used     VARCHAR(50) NULL,
    prompt_version VARCHAR(20) NULL,
    created_at     DATETIME NOT NULL,
    edited_at      DATETIME NULL,
    approved_by    INT NULL,
    approved_at    DATETIME NULL,
    UNIQUE KEY uq_minutes_version (meeting_id, version),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    FOREIGN KEY (approved_by) REFERENCES users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Migration แบบมีเวอร์ชัน (CREATE TABLE IF NOT EXISTS ข้างบนไม่เพิ่มคอลัมน์ใหม่ให้ตารางที่มีอยู่แล้ว)
# เพิ่มของใหม่ต่อท้ายด้วยเลขเวอร์ชันถัดไปเท่านั้น ห้ามแก้/แทรกของเดิม — เวอร์ชันที่รันแล้วถูกบันทึกใน
# ตาราง schema_migrations และจะไม่รันซ้ำ
_MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, ["ALTER TABLE action_items ADD COLUMN due_time TIME NULL"]),
    (2, ["ALTER TABLE action_items ADD COLUMN due_time_end TIME NULL"]),
    (3, [
        "ALTER TABLE meetings ADD COLUMN owner_user_id INT NULL",
        "ALTER TABLE meetings ADD CONSTRAINT fk_meetings_owner FOREIGN KEY (owner_user_id) "
        "REFERENCES users(user_id) ON DELETE SET NULL",
    ]),
    (4, [  # ข้อมูลหัวรายงานการประชุม
        "ALTER TABLE meetings ADD COLUMN venue VARCHAR(255) NULL",
        "ALTER TABLE meetings ADD COLUMN scheduled_at DATETIME NULL",
        "ALTER TABLE meetings ADD COLUMN meeting_no VARCHAR(50) NULL",
        "ALTER TABLE meetings ADD COLUMN org_name VARCHAR(255) NULL",
    ]),
    (5, [  # ผู้พูดใน caption ผูกกับผู้เข้าร่วมที่ลงทะเบียนไว้
        "ALTER TABLE speakers ADD COLUMN participant_id INT NULL",
        "ALTER TABLE speakers ADD CONSTRAINT fk_speakers_participant FOREIGN KEY (participant_id) "
        "REFERENCES participants(participant_id) ON DELETE SET NULL",
    ]),
    (6, [  # เก็บข้อความต้นฉบับไว้เสมอเมื่อมีการแก้ transcript; ลบแบบ soft delete
        "ALTER TABLE transcript_segments ADD COLUMN original_text TEXT NULL",
        "ALTER TABLE transcript_segments ADD COLUMN edited_at DATETIME NULL",
        "ALTER TABLE transcript_segments ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT FALSE",
    ]),
    (7, [  # สถานะประชุมเดิม (in_progress/completed) -> สถานะใหม่ของ workflow รายงาน
        "UPDATE meetings SET status = 'recording' WHERE status = 'in_progress'",
        "UPDATE meetings SET status = 'transcript_review' WHERE status = 'completed'",
        "ALTER TABLE meetings ALTER COLUMN status SET DEFAULT 'recording'",
    ]),
]

# workflow ของการประชุม: recording -> transcript_review -> transcript_verified -> draft -> approved
# approved คือสถานะสุดท้าย (ล็อก) การแก้หลังอนุมัติต้องสร้างรายงานเวอร์ชันใหม่ในอนาคต ไม่ย้อนสถานะ
MEETING_STATUSES = ("recording", "transcript_review", "transcript_verified", "draft", "approved")
_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "recording": {"transcript_review"},
    "transcript_review": {"transcript_verified"},
    "transcript_verified": {"transcript_review", "draft"},
    "draft": {"transcript_review", "draft", "approved"},
    "approved": set(),
}

ROLES = ("chair", "secretary", "attendee")          # ประธาน / เลขา / ผู้เข้าร่วม
UNIQUE_ROLES = ("chair", "secretary")               # แต่ละการประชุมมีได้คนเดียว
ATTENDANCE = ("invited", "present", "absent")       # เชิญไว้ (ยังไม่ยืนยัน) / เข้าร่วม / ไม่มา

# error ที่แปลว่า "ของนี้มีอยู่แล้ว" — เกิดกับ DB เก่าที่ถูก ALTER ไปแล้วก่อนมีระบบ schema_migrations
_ALREADY_APPLIED_ERRORS = {
    1060,  # Duplicate column name
    1061,  # Duplicate key name
    1826,  # Duplicate foreign key constraint name
}


def _fmt_time(value) -> str | None:
    """pymysql คืนคอลัมน์ TIME มาเป็น datetime.timedelta — แปลงเป็น 'HH:MM' ให้ JSON อ่านง่าย"""
    if value is None:
        return None
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


def init_schema():
    """สร้างตารางทั้งหมดถ้ายังไม่มี (เรียกครั้งเดียวตอนแอปสตาร์ท)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for statement in _SCHEMA.split(";"):
                statement = statement.strip()
                if statement:
                    cur.execute(statement)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version    INT PRIMARY KEY,
                       applied_at DATETIME NOT NULL
                   ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
            )
            cur.execute("SELECT version FROM schema_migrations")
            applied = {row["version"] for row in cur.fetchall()}
            for version, statements in _MIGRATIONS:
                if version in applied:
                    continue
                for statement in statements:
                    try:
                        cur.execute(statement)
                    except (pymysql.err.OperationalError, pymysql.err.InternalError) as e:
                        if e.args[0] not in _ALREADY_APPLIED_ERRORS:
                            raise
                cur.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, NOW())", (version,)
                )
    finally:
        conn.close()


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


def create_meeting(meet_url: str, owner_user_id: int) -> int:
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


_YOU_SUFFIX_RE = re.compile(r"\s*\((you|คุณ)\)\s*$", re.I)


def normalize_name(name: str) -> str:
    """ทำชื่อให้เทียบกันได้: ตัดช่องว่างซ้ำ, ไม่สนตัวพิมพ์เล็ก/ใหญ่, ตัดต่อท้าย "(You)"/"(คุณ)" ที่ Meet เติมให้ตัวเอง"""
    name = _YOU_SUFFIX_RE.sub("", name or "")
    return re.sub(r"\s+", " ", name).strip().casefold()


def _match_participant(cur, meeting_id: int, display_name: str) -> int | None:
    """หา participant ที่ชื่อตรงกับชื่อใน caption — ต้องตรงกันคนเดียวเท่านั้น (ชื่อซ้ำกัน = ไม่เดา ให้คนเลือกเอง)"""
    cur.execute(
        "SELECT participant_id, display_name FROM participants WHERE meeting_id = %s", (meeting_id,)
    )
    target = normalize_name(display_name)
    if not target:
        return None
    matches = [r["participant_id"] for r in cur.fetchall() if normalize_name(r["display_name"]) == target]
    return matches[0] if len(matches) == 1 else None


def _mark_present(cur, participant_id: int):
    cur.execute(
        "UPDATE participants SET attendance = 'present' WHERE participant_id = %s AND attendance = 'invited'",
        (participant_id,),
    )


def get_or_create_speaker(meeting_id: int, display_name: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT speaker_id FROM speakers WHERE meeting_id = %s AND display_name = %s",
                (meeting_id, display_name),
            )
            row = cur.fetchone()
            if row:
                return row["speaker_id"]
            participant_id = _match_participant(cur, meeting_id, display_name)
            cur.execute(
                "INSERT INTO speakers (meeting_id, display_name, participant_id) VALUES (%s, %s, %s)",
                (meeting_id, display_name, participant_id),
            )
            speaker_id = cur.lastrowid
            if participant_id is not None:
                _mark_present(cur, participant_id)
            return speaker_id
    finally:
        conn.close()


def list_speakers(meeting_id: int) -> list[dict]:
    """ผู้พูดที่ปรากฏใน caption พร้อมผู้เข้าร่วมที่ผูกอยู่ (ถ้ามี) และจำนวนช่วงคำพูด — ไว้ให้คนจับคู่ชื่อที่ยังไม่รู้จัก"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.speaker_id, s.display_name, s.participant_id,
                          COUNT(t.segment_id) AS segment_count
                   FROM speakers s
                   LEFT JOIN transcript_segments t ON t.speaker_id = s.speaker_id AND t.deleted = FALSE
                   WHERE s.meeting_id = %s
                   GROUP BY s.speaker_id, s.display_name, s.participant_id
                   ORDER BY s.speaker_id""",
                (meeting_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def link_speaker(speaker_id: int, participant_id: int | None):
    """ผูกผู้พูด (ชื่อใน caption) เข้ากับผู้เข้าร่วม หรือ None เพื่อยกเลิกการผูก
    ผู้เข้าร่วมที่ถูกผูกและยังเป็น 'invited' จะกลายเป็น 'present' (เพราะมีเสียงพูดจริง)
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT meeting_id FROM speakers WHERE speaker_id = %s", (speaker_id,))
            speaker = cur.fetchone()
            if not speaker:
                raise ValueError("ไม่พบผู้พูดนี้")
            if participant_id is not None:
                cur.execute(
                    "SELECT meeting_id FROM participants WHERE participant_id = %s", (participant_id,)
                )
                p = cur.fetchone()
                if not p or p["meeting_id"] != speaker["meeting_id"]:
                    raise ValueError("ผู้เข้าร่วมนี้ไม่ได้อยู่ในการประชุมเดียวกับผู้พูด")
            cur.execute(
                "UPDATE speakers SET participant_id = %s WHERE speaker_id = %s", (participant_id, speaker_id)
            )
            if participant_id is not None:
                _mark_present(cur, participant_id)
    finally:
        conn.close()


def auto_link_speakers(meeting_id: int) -> int:
    """จับคู่ผู้พูดที่ยังไม่ผูกกับผู้เข้าร่วมตามชื่ออีกครั้ง (เช่น เพิ่มรายชื่อผู้เข้าร่วมหลังประชุมเริ่มไปแล้ว)
    คืนจำนวนผู้พูดที่ผูกได้ใหม่
    """
    conn = get_connection()
    try:
        linked = 0
        with conn.cursor() as cur:
            cur.execute(
                "SELECT speaker_id, display_name FROM speakers WHERE meeting_id = %s AND participant_id IS NULL",
                (meeting_id,),
            )
            for sp in cur.fetchall():
                pid = _match_participant(cur, meeting_id, sp["display_name"])
                if pid is not None:
                    cur.execute(
                        "UPDATE speakers SET participant_id = %s WHERE speaker_id = %s", (pid, sp["speaker_id"])
                    )
                    _mark_present(cur, pid)
                    linked += 1
        return linked
    finally:
        conn.close()


# ── ผู้เข้าร่วมประชุม + บทบาท ──

def _check_participant_fields(role: str | None, attendance: str | None):
    if role is not None and role not in ROLES:
        raise ValueError(f"บทบาทไม่ถูกต้อง: {role} (ต้องเป็น {', '.join(ROLES)})")
    if attendance is not None and attendance not in ATTENDANCE:
        raise ValueError(f"สถานะการเข้าร่วมไม่ถูกต้อง: {attendance} (ต้องเป็น {', '.join(ATTENDANCE)})")


def _check_role_free(cur, meeting_id: int, role: str, except_participant_id: int | None = None):
    """ประธาน/เลขามีได้คนเดียวต่อการประชุม — ถ้ามีคนถือบทบาทนี้อยู่แล้วให้แจ้งก่อนเขียนทับโดยไม่รู้ตัว"""
    if role not in UNIQUE_ROLES:
        return
    cur.execute(
        "SELECT participant_id, display_name FROM participants WHERE meeting_id = %s AND role = %s",
        (meeting_id, role),
    )
    for r in cur.fetchall():
        if r["participant_id"] != except_participant_id:
            label = "ประธาน" if role == "chair" else "เลขา"
            raise ValueError(f"การประชุมนี้มี{label}แล้ว ({r['display_name']}) — เปลี่ยนบทบาทของคนนั้นก่อน")


def add_participant(
    meeting_id: int,
    display_name: str,
    email: str | None = None,
    role: str = "attendee",
    attendance: str = "invited",
    user_id: int | None = None,
) -> int:
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("ต้องระบุชื่อผู้เข้าร่วม")
    _check_participant_fields(role, attendance)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _check_role_free(cur, meeting_id, role)
            cur.execute(
                """INSERT INTO participants
                   (meeting_id, display_name, email, role, attendance, user_id, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                (meeting_id, display_name, (email or "").strip() or None, role, attendance, user_id),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_participant(participant_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM participants WHERE participant_id = %s", (participant_id,))
            return cur.fetchone()
    finally:
        conn.close()


def list_participants(meeting_id: int) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM participants WHERE meeting_id = %s ORDER BY participant_id", (meeting_id,)
            )
            return list(cur.fetchall())  # pymysql คืน tuple ว่างเมื่อไม่มีแถว — ทำให้ชนิดคงที่
    finally:
        conn.close()


_PARTICIPANT_EDITABLE = ("display_name", "email", "role", "attendance")


def update_participant(participant_id: int, **fields) -> None:
    unknown = set(fields) - set(_PARTICIPANT_EDITABLE)
    if unknown:
        raise ValueError(f"แก้ฟิลด์นี้ไม่ได้: {', '.join(sorted(unknown))}")
    if not fields:
        return
    if "display_name" in fields:
        fields["display_name"] = (fields["display_name"] or "").strip()
        if not fields["display_name"]:
            raise ValueError("ต้องระบุชื่อผู้เข้าร่วม")
    if "email" in fields:
        fields["email"] = (fields["email"] or "").strip() or None
    _check_participant_fields(fields.get("role"), fields.get("attendance"))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT meeting_id FROM participants WHERE participant_id = %s", (participant_id,))
            row = cur.fetchone()
            if not row:
                raise ValueError("ไม่พบผู้เข้าร่วมนี้")
            if "role" in fields:
                _check_role_free(cur, row["meeting_id"], fields["role"], except_participant_id=participant_id)
            sets = ", ".join(f"{col} = %s" for col in fields)
            cur.execute(
                f"UPDATE participants SET {sets} WHERE participant_id = %s",
                (*fields.values(), participant_id),
            )
    finally:
        conn.close()


def delete_participant(participant_id: int) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM participants WHERE participant_id = %s", (participant_id,))
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
    """แก้ไขข้อความของ segment เดิม (ต่อความยาวขึ้นระหว่างที่คนคนเดิมพูดต่อ แทนการ insert แถวใหม่)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE transcript_segments SET text = %s WHERE segment_id = %s",
                (text, segment_id),
            )
    finally:
        conn.close()


def list_meetings(owner_user_id: int, limit: int = 50) -> list[dict]:
    """รายการการประชุมล่าสุดของผู้ใช้คนนี้ พร้อมสรุปย่อ (ถ้ามี) สำหรับหน้า "การประชุมที่ผ่านมา\""""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.meeting_id, m.meet_url, m.title, m.started_at, m.ended_at, m.status,
                          s.executive_summary
                   FROM meetings m
                   LEFT JOIN summaries s ON s.meeting_id = m.meeting_id
                   WHERE m.owner_user_id = %s
                   ORDER BY m.started_at DESC
                   LIMIT %s""",
                (owner_user_id, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_meeting(meeting_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT meeting_id, meet_url, title, venue, scheduled_at, meeting_no, org_name,
                          started_at, ended_at, status, owner_user_id
                   FROM meetings WHERE meeting_id = %s""",
                (meeting_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def get_transcript(meeting_id: int) -> list[dict]:
    """ดึง transcript ทั้งหมดของการประชุมนี้ เรียงตามลำดับการพูด พร้อมชื่อผู้พูด"""
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
            return cur.fetchall()
    finally:
        conn.close()


def insert_summary(meeting_id: int, executive_summary: str, model_used: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO summaries (meeting_id, executive_summary, model_used, generated_at)
                   VALUES (%s, %s, %s, NOW())""",
                (meeting_id, executive_summary, model_used),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_summary(meeting_id: int) -> dict | None:
    """ถ้าการประชุมนี้สรุปไปแล้ว คืนสรุป + action items เดิม ไม่มีคืน None"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT summary_id, meeting_id, executive_summary, model_used FROM summaries WHERE meeting_id = %s",
                (meeting_id,),
            )
            summary = cur.fetchone()
            if not summary:
                return None
            cur.execute(
                """SELECT action_item_id, description, assignee, due_date, due_time, due_time_end,
                          calendar_synced
                   FROM action_items WHERE summary_id = %s""",
                (summary["summary_id"],),
            )
            items = cur.fetchall()
            for item in items:
                item["due_time"] = _fmt_time(item["due_time"])
                item["due_time_end"] = _fmt_time(item["due_time_end"])
            summary["action_items"] = items
            return summary
    finally:
        conn.close()


def delete_summary(meeting_id: int):
    """ลบสรุป + action items เดิมของการประชุมนี้ (เผื่ออยากให้ Gemini สรุปใหม่)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM summaries WHERE meeting_id = %s", (meeting_id,))
    finally:
        conn.close()


def insert_action_item(
    summary_id: int,
    description: str,
    assignee: str | None,
    due_date: str | None,
    due_time: str | None = None,
    due_time_end: str | None = None,
) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO action_items (summary_id, description, assignee, due_date, due_time, due_time_end)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (summary_id, description, assignee, due_date, due_time, due_time_end),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_action_item(action_item_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT action_item_id, summary_id, description, assignee, due_date, due_time,
                          due_time_end, calendar_synced, google_calendar_event_id
                   FROM action_items WHERE action_item_id = %s""",
                (action_item_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ── รายงานการประชุม (minutes) แบบมีเวอร์ชัน — เนื้อหาเป็น JSON ตาม schema ที่กำหนดในขั้น AI ──

def insert_minutes(
    meeting_id: int, content: dict, model_used: str | None = None, prompt_version: str | None = None
) -> dict:
    """เพิ่มรายงานเวอร์ชันใหม่ (draft) ไม่ทับเวอร์ชันเดิม คืน {minutes_id, version}"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM minutes WHERE meeting_id = %s", (meeting_id,)
            )
            version = cur.fetchone()["v"]
            cur.execute(
                """INSERT INTO minutes
                   (meeting_id, version, status, content, model_used, prompt_version, created_at)
                   VALUES (%s, %s, 'draft', %s, %s, %s, NOW())""",
                (meeting_id, version, json.dumps(content, ensure_ascii=False), model_used, prompt_version),
            )
            return {"minutes_id": cur.lastrowid, "version": version}
    finally:
        conn.close()


def get_latest_minutes(meeting_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM minutes WHERE meeting_id = %s ORDER BY version DESC LIMIT 1", (meeting_id,)
            )
            row = cur.fetchone()
            if row:
                row["content"] = json.loads(row["content"])
            return row
    finally:
        conn.close()


def get_action_item_owner(action_item_id: int) -> int | None:
    """user_id เจ้าของการประชุมที่ action item นี้อยู่ (None ถ้าไม่พบ item หรือประชุมไม่มีเจ้าของ)"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.owner_user_id
                   FROM action_items a
                   JOIN summaries s ON s.summary_id = a.summary_id
                   JOIN meetings m ON m.meeting_id = s.meeting_id
                   WHERE a.action_item_id = %s""",
                (action_item_id,),
            )
            row = cur.fetchone()
            return row["owner_user_id"] if row else None
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
