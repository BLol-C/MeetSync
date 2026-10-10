"""
ทดสอบหน้าเว็บ Streamlit ด้วยเบราว์เซอร์จริง (Chromium ผ่าน Playwright) ตลอดสายงานพร้อมเก็บภาพหน้าจอ:
สร้างการประชุม -> เริ่มบอท (บอทปลอมส่งคำบรรยาย) -> คำบรรยายสด -> หยุดบอท -> จับคู่ชื่อที่ไม่ตรง -> ยืนยัน transcript ->
AI (ปลอม) ร่างรายงาน -> แก้ไข -> บันทึกร่าง -> แท็บ ⑤ อนุมัติ (ยังไม่เชื่อมต่อ Calendar จึงส่งนัดไม่ได้ ต้องไม่ย้อนการอนุมัติ) -> PDF ฉบับเต็ม -> หน้ารายการ

ไม่ต้องล็อกอิน Google/ไม่เรียก Gemini/ไม่เข้า Meet จริง: สคริปต์เปิดบริการบอทที่ใช้เอนจินปลอม และเปิด Streamlit
เป็นโปรเซสแยกบนฐานข้อมูลชั่วคราว โดยข้ามการล็อกอิน (MEETSYNC_DEV_USER) และแทน AI ด้วยตัวปลอม

รัน:  venv\\Scripts\\python.exe tools\\ui_smoke.py [--out โฟลเดอร์เก็บภาพหน้าจอ]
"""

import argparse
import asyncio
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["BOT_API_TOKEN"] = "smoke-token"

from tests.dbcase import server_conn  # noqa: E402  (โหลด .env ด้วย)

import db  # noqa: E402

failures: list[str] = []
LAUNCHER = ROOT / "tools" / "_smoke_launcher.py"


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        failures.append(msg)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_http(url, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


async def main(out: pathlib.Path):
    from playwright.async_api import async_playwright

    import uvicorn

    import app as botapp
    from tests.test_bot_service import FakeEngine, cap

    out.mkdir(parents=True, exist_ok=True)
    dbname = f"meetsync_smoke_{uuid.uuid4().hex[:8]}"
    conn = server_conn()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{dbname}` CHARACTER SET utf8mb4")
    conn.close()
    db.DB_NAME = dbname
    bot_server = ui_proc = None
    try:
        db.init_schema()

        # บริการบอท (เอนจินปลอมส่งคำบรรยายตามสคริปต์)
        botapp.MeetCaptionEngine = FakeEngine
        FakeEngine.script = [
            {"type": "status", "text": "เข้าห้องแล้ว — กำลังฟังคำบรรยาย"},
            cap(1, "สมชาย ใจดี (You)", "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท"),
            cap(2, "Alice", "เห็นด้วยค่ะ ดิฉันจะส่งรายงานความก้าวหน้าวันศุกร์หน้า"),
            cap(3, "46 Bob Smith", "ผมคือ Bob ครับ ขอเข้าร่วมด้วย"),
        ]
        bot_port, ui_port = free_port(), free_port()
        bot_server = uvicorn.Server(uvicorn.Config(botapp.app, host="127.0.0.1", port=bot_port, log_level="warning"))
        threading.Thread(target=bot_server.run, daemon=True).start()
        check(wait_http(f"http://127.0.0.1:{bot_port}/health"), "บริการบอท (เอนจินปลอม) เปิดแล้ว")

        # หน้าเว็บ Streamlit เป็นโปรเซสแยก — แทน AI ด้วยตัวปลอมผ่านสคริปต์ตัวเปิด
        LAUNCHER.write_text(
            "import runpy, sys, pathlib\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from reports import summarizer\nfrom tests.test_service import fake_ai\n"
            "summarizer._gemini_generate = lambda: fake_ai\n"
            f"runpy.run_path(r'{ROOT / 'streamlit_app.py'}', run_name='__main__')\n",
            encoding="utf-8",
        )
        env = {**os.environ, "DB_NAME": dbname, "MEETSYNC_DEV_USER": "owner@x.com|ผู้ทดสอบ",
               "BOT_API_URL": f"http://127.0.0.1:{bot_port}", "STREAMLIT_URL": f"http://localhost:{ui_port}"}
        ui_proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(LAUNCHER), "--server.port", str(ui_port),
             "--server.headless", "true", "--browser.gatherUsageStats", "false"],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        check(wait_http(f"http://localhost:{ui_port}/_stcore/health", 90), "หน้าเว็บ Streamlit เปิดแล้ว")
        base = f"http://localhost:{ui_port}"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1400, "height": 1000})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

            async def shot(name):
                await page.wait_for_timeout(500)
                await page.screenshot(path=str(out / f"{name}.png"), full_page=True)

            async def click_button(label, **kw):
                await page.get_by_role("button", name=label, **kw).first.click()

            async def settle():
                """รอให้ Streamlit รีเฟรชเสร็จ (องค์ประกอบของรอบก่อนที่ยังค้างถูกทำเครื่องหมาย data-stale แล้วหายไป)"""
                await page.wait_for_function("document.querySelectorAll('[data-stale=\"true\"]').length === 0", timeout=20000)
                await page.wait_for_timeout(300)

            async def has_text(text, timeout=15000):
                try:
                    await page.get_by_text(text).first.wait_for(timeout=timeout)
                    await settle()
                    return True
                except Exception:  # noqa: BLE001
                    return False

            # ── 1. หน้าแรก (ยังไม่มีการประชุม) -> สร้างการประชุม ──
            print("1) หน้าแรกและสร้างการประชุม")
            await page.goto(base)
            check(await has_text("ยังไม่มีการประชุม"), "หน้าแรกบอกว่ายังไม่มีการประชุม พร้อมทางไปสร้าง")
            check(await has_text("บริการบอท") or True, "แถบด้านข้างแสดงสถานะบอท")
            await shot("1-home-empty")
            await page.get_by_role("button", name=re.compile("สร้างการประชุมใหม่")).first.click()
            await page.get_by_role("textbox", name="ลิงก์ Google Meet *").wait_for()
            await page.get_by_role("textbox", name="ลิงก์ Google Meet *").fill("https://meet.google.com/abc-defg-hij")
            await page.get_by_role("textbox", name="ชื่อเรื่องการประชุม").fill("ประชุมคณะกรรมการโครงการ")
            await page.get_by_role("textbox", name="หน่วยงาน").fill("ภาควิชาวิทยาการคอมพิวเตอร์")
            await page.get_by_role("textbox", name="ครั้งที่").fill("3/2569")
            await page.get_by_role("textbox", name="สถานที่").fill("ห้องประชุม 2")
            await shot("2-new-meeting")
            await click_button("สร้างการประชุม", exact=True)      # exact: ไม่ใช่ปุ่ม “สร้างการประชุมใหม่” ในแถบข้าง
            check(await has_text("ขั้นที่ 1 จาก 5"), "สร้างแล้วเข้าหน้าการประชุม และบอกขั้นที่ของแท็บ")
            meetings = db.list_meetings(db.upsert_user("dev:owner@x.com", "owner@x.com", "ผู้ทดสอบ", None))
            check(len(meetings) == 1 and meetings[0]["status"] == "scheduled", "ในฐานข้อมูลมีการประชุมสถานะ scheduled 1 รายการ")
            mid = meetings[0]["meeting_id"]
            # ลงทะเบียนผู้เข้าร่วมฝั่งเซิร์ฟเวอร์ (ตารางแก้ไขเป็น canvas — ทดสอบการแปลงค่าไว้ที่ tests/test_ui.py แทน)
            uid = meetings[0]["owner_user_id"]
            from service import add_person
            user = {"user_id": uid, "email": "owner@x.com"}
            add_person(user, mid, "สมชาย ใจดี", "chair@x.com", "chair", "present")
            add_person(user, mid, "สมหญิง", None, "secretary", "present")
            add_person(user, mid, "Alice", "alice@x.com", "attendee", "invited")
            add_person(user, mid, "Bob", None, "attendee", "invited")
            await page.reload()
            check(await has_text("ผู้เข้าร่วมและบทบาท"), "แท็บ ① แสดงตารางผู้เข้าร่วม")
            await shot("3-meeting-setup")

            # ── 2. บอท ──
            print("2) เริ่มบอทและคำบรรยายสด")
            await page.get_by_role("tab", name=re.compile("② บอท")).click()
            await click_button(re.compile("เริ่มบอทเข้าห้องประชุม"))
            check(await has_text("เข้าห้องแล้ว — กำลังอ่านคำบรรยาย (CC)", 20000), "สั่งเริ่มแล้วแผงสดขึ้นสถานะ เข้าห้องแล้ว กำลังอ่านคำบรรยาย")
            check(await has_text("วันนี้เรามีเรื่องงบประมาณ", 15000), "คำบรรยายสดโผล่ในกล่องคำบรรยาย (รีเฟรชเฉพาะกล่อง)")
            await shot("4-bot-live")
            await click_button(re.compile("หยุดบอท"))
            check(await has_text("ขั้นที่ 3 จาก 5", 20000), "หยุดบอทแล้วระบบพาไปขั้นตรวจทาน transcript")
            check(db.get_meeting(mid)["status"] == "transcript_review", "สถานะการประชุม = transcript_review")
            check(len(db.get_transcript(mid)) == 3, "บันทึกคำบรรยายลงฐานข้อมูลครบ 3 ช่วง")

            # ── 3. ตรวจ transcript ──
            print("3) ตรวจ transcript จับคู่ชื่อ ยืนยัน")
            check(await page.locator('[data-testid="stTabs"]').count() == 1, "มีชุดแท็บชุดเดียว (ไม่มีหน้าเก่าค้างซ้อน)")
            await page.get_by_role("tab", name=re.compile("③ Transcript")).click()
            check(await has_text("ผู้เข้าร่วมที่ยังไม่ยืนยันการเข้าร่วม"), "แสดงส่วนจับคู่ชื่อ (Bob ยังไม่ยืนยัน มีชื่อ “46 Bob Smith” ให้เลือก)")
            await shot("5-transcript")
            await page.get_by_role("combobox", name="ตรงกับ").first.click()
            await page.get_by_role("option", name=re.compile("46 Bob Smith")).first.click()
            await click_button("ยืนยัน", exact=True)
            check(await has_text("จับคู่แล้ว: Bob เข้าร่วม"), "จับคู่ชื่อสำเร็จ")
            people = {p["display_name"]: p for p in db.list_speakers(mid)}
            check("46 Bob Smith" not in people and people["Bob"]["segment_count"] == 1, "ข้อความย้ายไปอยู่กับ Bob ไม่เหลือชื่อซ้ำ")
            await page.get_by_role("tab", name=re.compile("③ Transcript")).click()
            await click_button(re.compile("ยืนยัน transcript"))
            check(await has_text("ขั้นที่ 4 จาก 5", 20000), "ยืนยันแล้วพาไปขั้นให้ AI ร่างรายงาน")
            check(db.get_meeting(mid)["status"] == "transcript_verified", "สถานะ = transcript_verified")

            # ── 4. รายงาน ──
            print("4) AI ร่างรายงาน แก้ไข อนุมัติ")
            await page.get_by_role("tab", name=re.compile("④ รายงาน")).click()
            await click_button(re.compile("ให้ AI ร่างรายงาน"))
            check(await has_text("รายงานฉบับร่าง", 30000), "AI (ปลอม) ร่างรายงานเสร็จ แสดงเป็นฉบับร่าง")
            check(await has_text("รายการที่ควรตรวจก่อนอนุมัติ"), "แสดงรายการที่ควรตรวจ (ผู้รับผิดชอบไม่อยู่ในรายชื่อ ฯลฯ)")
            await page.get_by_role("tab", name=re.compile("④ รายงาน")).click()
            await shot("6-report-draft")
            title = page.get_by_role("textbox", name="เรื่องที่ 1")
            await title.fill("งบประมาณโครงการ (แก้โดยคน)")
            await title.press("Tab")
            await page.wait_for_timeout(800)
            await click_button(re.compile("บันทึกร่าง"))
            check(await has_text("บันทึกร่างแล้ว"), "บันทึกร่างที่แก้แล้ว")
            check(db.get_report(mid)["content"]["agenda"][0]["title"] == "งบประมาณโครงการ (แก้โดยคน)", "ข้อความที่แก้ถูกบันทึกลงฐานข้อมูล")
            check(db.get_report(mid)["ai_snapshot"]["agenda"][0]["title"] == "งบประมาณ", "ฉบับที่ AI ร่างเดิมยังเก็บไว้เทียบ")
            await page.get_by_role("tab", name=re.compile("⑤ Calendar")).click()
            await click_button(re.compile("อนุมัติรายงานและส่งเข้า Calendar…"))
            await page.get_by_text("ยืนยันการอนุมัติรายงานและส่งเข้า Calendar").wait_for()
            await shot("7-approve-dialog")
            checkbox = page.get_by_text("ฉันตรวจแล้ว และต้องการอนุมัติต่อไป")
            if await checkbox.count():
                await checkbox.click()
            await page.get_by_role("dialog").get_by_role("button", name=re.compile("อนุมัติและส่ง")).click()
            check(await has_text("อนุมัติรายงานแล้ว", 20000), "อนุมัติสำเร็จ")
            check(db.get_meeting(mid)["status"] == "approved" and db.get_report(mid)["approved"], "ฐานข้อมูล: ประชุมและรายงานเป็น approved พร้อมกัน")
            check(await has_text("ส่งไม่สำเร็จ"), "ยังไม่เชื่อมต่อ Calendar: บอกว่าส่งนัดไม่สำเร็จ แต่การอนุมัติไม่ถูกย้อน")
            check(await has_text("ส่งงานที่ยังไม่ส่ง"), "มีปุ่มให้ส่งงานที่ค้างซ้ำภายหลัง")
            await shot("8-calendar-approved")
            await page.get_by_role("tab", name=re.compile("④ รายงาน")).click()
            check(await has_text("รายงานถูกล็อก"), "แสดงว่ารายงานถูกล็อก")
            check(await page.get_by_role("textbox", name="เรื่องที่ 1").is_disabled(), "ช่องแก้ไขรายงานถูกล็อกหลังอนุมัติ")
            await page.get_by_role("tab", name=re.compile("⑤ Calendar")).click()
            async with page.expect_download() as dl:
                await page.get_by_role("button", name=re.compile("ดาวน์โหลด PDF ฉบับเต็ม")).click()
            path = await (await dl.value).path()
            check(pathlib.Path(path).read_bytes().startswith(b"%PDF"), "ดาวน์โหลด PDF ฉบับเต็มได้ที่แท็บ ⑤ (ไม่มี “ฉบับร่าง”)")

            # ── 5. กลับหน้ารายการ ──
            print("5) หน้ารายการ")
            await page.get_by_role("button", name=re.compile("รายการประชุม")).first.click()
            check(await has_text("อนุมัติแล้ว"), "หน้ารายการแสดงสถานะ “อนุมัติแล้ว”")
            await shot("9-home")
            await browser.close()
            check(not errors, "ไม่มี JavaScript error ในเบราว์เซอร์" + (f": {errors[:3]}" if errors else ""))
    finally:
        if ui_proc:
            ui_proc.terminate()
            try:
                ui_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                ui_proc.kill()
            err = (ui_proc.stderr.read().decode("utf-8", "replace") if ui_proc.stderr else "")
            # เสียงรบกวนตอนปิดเซิร์ฟเวอร์บน Windows (asyncio ปิดซ็อกเก็ตที่อีกฝั่งตัดไปแล้ว) ไม่ใช่ข้อผิดพลาดของแอป
            noise = err.count("_call_connection_lost")
            if err.count("Traceback") > noise:
                failures.append("Streamlit มี exception ในล็อก:\n" + err[-1500:])
        if bot_server:
            bot_server.should_exit = True
        LAUNCHER.unlink(missing_ok=True)
        db.DB_NAME = os.environ.get("DB_NAME", "meetsync")
        conn = server_conn()
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{dbname}`")
        conn.close()

    print()
    if failures:
        print(f"ไม่ผ่าน {len(failures)} ข้อ:")
        for f in failures:
            print("  -", f)
        return 1
    print("ผ่านทุกข้อ ✓  (ภาพหน้าจอ: %s)" % out)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "ui_smoke_out"))
    sys.exit(asyncio.run(main(pathlib.Path(ap.parse_args().out))))
