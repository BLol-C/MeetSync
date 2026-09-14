"""
เวอร์ชัน CLI ของ MeetSync — อ่านคำบรรยาย (CC) ของ Google Meet ลง terminal

  venv\\Scripts\\python.exe caption_bot.py [ลิงก์ Meet]

ถ้าไม่ใส่ลิงก์ จะใช้ MEET_URL ด้านล่าง
แกนหลักอยู่ใน meet_engine.py (ใช้ร่วมกับเว็บแอป app.py)
"""

import asyncio
import sys
from datetime import datetime

from meet_engine import MeetCaptionEngine

MEET_URL = "https://meet.google.com/khn-anxh-hvz"

# LIVE = True  -> อัปเดตข้อความบรรทัดเดียวแบบเรียลไทม์ แล้ว "ค้าง" เมื่อคนต่อไปเริ่มพูด
# LIVE = False -> พิมพ์เฉพาะประโยคที่นิ่งแล้ว เป็นบรรทัด ๆ
LIVE = True


def _now():
    return datetime.now().strftime("%H:%M:%S")


class CaptionPrinter:
    def __init__(self):
        self.current_id = None
        self.prefix = ""
        self.line_len = 0
        self.last_final = {}

    def _commit(self):
        if self.line_len:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self.line_len = 0

    def on_event(self, ev):
        kind = ev.get("type")
        if kind == "status":
            self._commit()
            print(f"— {ev['text']}", flush=True)
            return
        if kind == "error":
            self._commit()
            print(f"❌ {ev['text']}", flush=True)
            return
        if kind != "caption":
            return

        name = (ev.get("name") or "ไม่ทราบชื่อ").strip()
        text = (ev.get("text") or "").strip()
        cid = ev.get("id")
        if not text:
            return

        if not LIVE or name == "(raw)":
            if ev.get("final") and self.last_final.get(name) != text:
                self.last_final[name] = text
                self._commit()
                print(f"[{_now()}] {name}: {text}", flush=True)
            return

        if self.current_id is not None and cid is not None and cid < self.current_id:
            return
        if cid != self.current_id:
            self._commit()
            self.current_id = cid
            self.prefix = _now()

        line = f"[{self.prefix}] {name}: {text}"
        pad = max(0, self.line_len - len(line))
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()
        self.line_len = len(line)


async def main(url: str):
    printer = CaptionPrinter()
    engine = MeetCaptionEngine(on_event=printer.on_event)
    engine.start(url)
    try:
        await engine.wait()
    except KeyboardInterrupt:
        await engine.stop()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else MEET_URL
    try:
        asyncio.run(main(target))
    except KeyboardInterrupt:
        print("\nจบการทำงาน")
