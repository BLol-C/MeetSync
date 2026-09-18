"""
บันทึกการประชุม/ผู้พูด/transcript ลง MySQL ให้ถาวร (แทนที่จะอยู่แค่ใน RAM)

ตั้งค่าการเชื่อมต่อผ่าน .env: DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
ต้องสร้างฐานข้อมูลเปล่าไว้ก่อน (เช่น `CREATE DATABASE meetsync;`) — ตารางข้างในสร้างให้
อัตโนมัติตอนเรียก init_schema()

ตาราง summaries/action_items สร้าง schema ไว้รอสำหรับขั้น AI Summarization ถัดไป ยังไม่มี
ฟังก์ชัน insert ให้ในไฟล์นี้
"""

import os

import pymysql
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
    status      VARCHAR(20) NOT NULL DEFAULT 'in_progress'
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
"""

# ตารางเก่าที่สร้างไปแล้วก่อนมี due_time (CREATE TABLE IF NOT EXISTS ข้างบนจะไม่เพิ่มคอลัมน์ใหม่ให้)
_MIGRATIONS = [
    "ALTER TABLE action_items ADD COLUMN due_time TIME NULL",
    "ALTER TABLE action_items ADD COLUMN due_time_end TIME NULL",
]


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
            for statement in _MIGRATIONS:
                try:
                    cur.execute(statement)
                except pymysql.err.OperationalError as e:
                    if e.args[0] != 1060:  # 1060 = Duplicate column name (migration ทำไปแล้ว)
                        raise
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


def create_meeting(meet_url: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meetings (meet_url, started_at, status) VALUES (%s, NOW(), 'in_progress')",
                (meet_url,),
            )
            return cur.lastrowid
    finally:
        conn.close()


def end_meeting(meeting_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE meetings SET ended_at = NOW(), status = 'completed' WHERE meeting_id = %s",
                (meeting_id,),
            )
    finally:
        conn.close()


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
            cur.execute(
                "INSERT INTO speakers (meeting_id, display_name) VALUES (%s, %s)",
                (meeting_id, display_name),
            )
            return cur.lastrowid
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


def list_meetings(limit: int = 50) -> list[dict]:
    """รายการการประชุมล่าสุด พร้อมสรุปย่อ (ถ้ามี) สำหรับหน้า "การประชุมที่ผ่านมา\""""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.meeting_id, m.meet_url, m.started_at, m.ended_at, m.status,
                          s.executive_summary
                   FROM meetings m
                   LEFT JOIN summaries s ON s.meeting_id = m.meeting_id
                   ORDER BY m.started_at DESC
                   LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_meeting(meeting_id: int) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT meeting_id, meet_url, started_at, ended_at, status FROM meetings WHERE meeting_id = %s",
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
                   WHERE t.meeting_id = %s
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
