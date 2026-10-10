"""อัปเกรดฐานข้อมูลเดิมให้ตรงกับโค้ดปัจจุบัน (ไม่ลบข้อมูลเดิม) — รันครั้งเดียว:

    .\\venv\\Scripts\\python.exe tools\\upgrade_calendar_link.py

ทำสามอย่าง (รันซ้ำได้ปลอดภัย):
  1. เพิ่มคอลัมน์ action_items.google_calendar_link (ลิงก์เปิดนัดใน Google Calendar)
  2. ลบคอลัมน์ action_items.calendar_synced ที่ซ้ำกับ google_calendar_event_id (มี event id = ส่งแล้ว)
  3. ลบตาราง calendar_tasks ที่เคยสร้างไว้ในรุ่นก่อนหน้า (ตอนนี้ส่งเข้า Calendar จาก action_items โดยตรง)
     — ลบเฉพาะเมื่อตารางว่าง ถ้ามีข้อมูลอยู่จะไม่แตะและแจ้งให้ทราบ
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")   # ต้องโหลดก่อน import db เพราะ db.py อ่านค่าเชื่อมต่อตอน import

import db  # noqa: E402


def main() -> int:
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                        "AND TABLE_NAME = 'action_items' AND COLUMN_NAME = 'google_calendar_link'")
            if cur.fetchone()["n"]:
                print("action_items.google_calendar_link: มีอยู่แล้ว")
            else:
                cur.execute("ALTER TABLE action_items ADD COLUMN google_calendar_link VARCHAR(500) NULL")
                print("action_items.google_calendar_link: เพิ่มแล้ว")
            cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                        "AND TABLE_NAME = 'action_items' AND COLUMN_NAME = 'calendar_synced'")
            if cur.fetchone()["n"]:
                cur.execute("SELECT COUNT(*) AS n FROM action_items WHERE calendar_synced = TRUE AND google_calendar_event_id IS NULL")
                if cur.fetchone()["n"]:
                    print("action_items.calendar_synced: มีแถวที่ติ๊กส่งแล้วแต่ไม่มี event id จึงยังไม่ลบคอลัมน์ (ตรวจข้อมูลก่อน)")
                else:
                    cur.execute("ALTER TABLE action_items DROP COLUMN calendar_synced")
                    print("action_items.calendar_synced: ลบคอลัมน์ที่ซ้ำกับ google_calendar_event_id แล้ว")
            else:
                print("action_items.calendar_synced: ไม่มี (ไม่ต้องทำอะไร)")
            cur.execute("SHOW TABLES LIKE 'calendar_tasks'")
            if cur.fetchone():
                cur.execute("SELECT COUNT(*) AS n FROM calendar_tasks")
                rows = cur.fetchone()["n"]
                if rows:
                    print(f"calendar_tasks: มีข้อมูล {rows} แถว จึงไม่ลบ (ตารางนี้ไม่ได้ใช้แล้ว ตรวจแล้วลบเองได้)")
                else:
                    cur.execute("DROP TABLE calendar_tasks")
                    print("calendar_tasks: ว่าง จึงลบตารางที่ไม่ใช้แล้วออก")
            else:
                print("calendar_tasks: ไม่มี (ไม่ต้องทำอะไร)")
    finally:
        conn.close()
    print(f"ฐานข้อมูล {db.DB_NAME}: พร้อมใช้งาน")
    return 0


if __name__ == "__main__":
    sys.exit(main())
