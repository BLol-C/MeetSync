"""เพิ่มตาราง calendar_tasks (แท็บ ⑤ Calendar) ให้ฐานข้อมูลเดิม โดยไม่ลบข้อมูลเดิม — รันครั้งเดียว:

    .\\venv\\Scripts\\python.exe tools\\add_calendar_tasks.py

ใช้การเชื่อมต่อเดียวกับแอป (อ่านจากไฟล์ .env) และรันซ้ำได้ปลอดภัย (CREATE TABLE IF NOT EXISTS)
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")   # ต้องโหลดก่อน import db เพราะ db.py อ่านค่าเชื่อมต่อตอน import

import db  # noqa: E402


def main():
    sql = (ROOT / "tools" / "add_calendar_tasks.sql").read_text(encoding="utf-8")
    statement = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")).strip().rstrip(";")
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
            cur.execute("SHOW TABLES LIKE 'calendar_tasks'")
            ok = cur.fetchone() is not None
    finally:
        conn.close()
    print(f"ฐานข้อมูล {db.DB_NAME}: ตาราง calendar_tasks " + ("พร้อมใช้งานแล้ว" if ok else "ยังไม่พบ — ตรวจสอบข้อความ error ด้านบน"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
