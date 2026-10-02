"""
ทดสอบหน้าเว็บด้วยเบราว์เซอร์จริง (Chromium ผ่าน Playwright) ตลอดสายงาน:
สร้างการประชุม -> ใส่ผู้เข้าร่วม/บทบาท -> ตรวจ/แก้ transcript -> ยืนยัน -> AI ร่างรายงาน (AI ปลอม) -> แก้ -> อนุมัติ -> PDF

ไม่ต้องล็อกอิน Google/ไม่เรียก Gemini: สคริปต์เปิดเซิร์ฟเวอร์ของแอปเองบนฐานข้อมูลชั่วคราว แล้วแทนที่การล็อกอินด้วยผู้ใช้ปลอม
(บอทเข้าห้อง Meet จริงไม่ได้ทดสอบที่นี่ — ใช้ transcript ที่จำลองใส่ลง DB แทน)

รัน:  venv\\Scripts\\python.exe tools\\ui_smoke.py [--out โฟลเดอร์เก็บภาพหน้าจอ]
"""

import argparse
import asyncio
import os
import pathlib
import re
import socket
import sys
import threading
import time
import uuid
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SESSION_SECRET", "ui-smoke")

from tests.dbcase import server_conn  # noqa: E402  (โหลด .env ด้วย)

import db  # noqa: E402

failures: list[str] = []


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


async def main(out: pathlib.Path):
    from playwright.async_api import async_playwright

    import app as appmod
    import summarizer
    from tests.test_api import fake_ai

    out.mkdir(parents=True, exist_ok=True)
    dbname = f"meetsync_test_{uuid.uuid4().hex[:8]}"
    conn = server_conn()
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{dbname}` CHARACTER SET utf8mb4")
    conn.close()
    db.DB_NAME = dbname
    server = None
    try:
        db.init_schema()
        user_id = db.upsert_user("ui-sub", "owner@x.com", "ผู้ทดสอบ", None)
        patches = [
            mock.patch.object(appmod, "_session_user",
                              lambda req: {"user_id": user_id, "email": "owner@x.com", "name": "ผู้ทดสอบ", "picture": None}),
            mock.patch.object(summarizer, "_gemini_generate", lambda: fake_ai),
        ]
        for p in patches:
            p.start()

        import uvicorn
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(appmod.app, host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.1)
        base = f"http://127.0.0.1:{port}"

        errors: list[str] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1100, "height": 1500})
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
            page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
            dialogs: list[str] = []

            async def on_dialog(d):
                dialogs.append(d.message)
                if d.type == "prompt":
                    await d.accept("")
                else:
                    await d.accept()
            page.on("dialog", on_dialog)

            # ── 1. สร้างการประชุม ──
            print("1) สร้างการประชุมและรายชื่อผู้เข้าร่วม")
            await page.goto(f"{base}/meeting/new")
            await page.fill("#f_url", "https://meet.google.com/abc-defg-hij")
            await page.fill("#f_title", "ประชุมคณะกรรมการโครงการ")
            await page.fill("#f_org", "ภาควิชาวิศวกรรมคอมพิวเตอร์")
            await page.fill("#f_no", "3/2569")
            await page.fill("#f_venue", "ห้องประชุม 2")
            rows = page.locator("#partBody tr")
            await rows.nth(0).locator("[data-f=display_name]").fill("สมชาย ใจดี")
            await rows.nth(0).locator("[data-f=email]").fill("chair@x.com")
            await rows.nth(1).locator("[data-f=display_name]").fill("สมหญิง รักงาน")
            await page.click("#addPart")      # วาดหน้าใหม่ — ข้อมูลที่พิมพ์ไว้ต้องไม่หาย
            check(await page.input_value("#f_title") == "ประชุมคณะกรรมการโครงการ", "เพิ่มผู้เข้าร่วมแล้วข้อมูลหัวฟอร์มไม่หาย")
            check(await page.locator("#partBody tr").nth(0).locator("[data-f=display_name]").input_value() == "สมชาย ใจดี",
                  "เพิ่มผู้เข้าร่วมแล้วชื่อที่พิมพ์ไว้ไม่หาย")
            rows = page.locator("#partBody tr")
            await rows.nth(2).locator("[data-f=display_name]").fill("Alice")
            await rows.nth(2).locator("[data-f=email]").fill("alice@x.com")
            await page.click("#addPart")
            await page.locator("#partBody tr").nth(3).locator("[data-f=display_name]").fill("Bob")
            await page.screenshot(path=str(out / "1-setup-new.png"))
            await page.click("#saveSetup")
            await page.wait_for_url(re.compile(r".*/meeting/\d+$"))
            meeting_id = int(page.url.rsplit("/", 1)[1])
            check(meeting_id > 0, f"สร้างการประชุมสำเร็จ (id={meeting_id}) และพาไปหน้าจัดการ")
            await page.wait_for_selector("#partBody tr")
            check(await page.locator("#partBody tr").count() == 4, "ผู้เข้าร่วมครบ 4 คนหลังบันทึก")
            check("ตั้งค่าไว้" in await page.text_content("#statusBadge"), "สถานะ = ตั้งค่าไว้ ยังไม่เริ่ม")
            check(await page.locator("a.btn", has_text="เริ่มบอท").count() == 1, "มีปุ่มเริ่มบอท")
            href = await page.locator("a.btn", has_text="เริ่มบอท").get_attribute("href")
            check(href == f"/?start={meeting_id}", f"ปุ่มเริ่มบอทชี้ไปหน้าสดพร้อม meeting_id ({href})")
            check(await page.locator(".tab.locked").count() == 2, "แท็บตรวจ transcript/รายงานยังถูกล็อก")

            # แก้ไขรายชื่อหลังสร้าง (บันทึกทันที)
            await page.locator("#partBody tr").nth(3).locator("[data-f=attendance]").select_option("absent")
            await page.wait_for_timeout(500)
            check(db.list_participants(meeting_id)[3]["attendance"] == "absent", "เปลี่ยนสถานะผู้เข้าร่วมแล้วบันทึกลง DB ทันที")
            await page.locator("#partBody tr").nth(2).locator("[data-f=role]").select_option("chair")
            await page.wait_for_selector(".toast.err")
            check("มีประธานแล้ว" in await page.text_content(".toast"), "ตั้งประธานซ้ำถูกปฏิเสธพร้อมข้อความชัดเจน")

            # ── 2. จำลองบอทบันทึกและจบการประชุม แล้วตรวจ transcript ──
            print("2) ตรวจและแก้ transcript")
            db.begin_recording(meeting_id)
            s1 = db.get_or_create_speaker(meeting_id, "สมชาย ใจดี (You)")
            s2 = db.get_or_create_speaker(meeting_id, "Alice")
            s3 = db.get_or_create_speaker(meeting_id, "คนแปลกหน้า")
            db.insert_segment(meeting_id, s1, 1, "วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท")
            db.insert_segment(meeting_id, s2, 2, "เห็นด้วยค่ะ ดิฉันจะส่งรายงานความก้าวหน้าวันศุกร์หน้า")
            db.insert_segment(meeting_id, s3, 3, "ทดสอบไมค์ ๆ")
            db.end_meeting(meeting_id)

            await page.goto(f"{base}/meeting/{meeting_id}")
            await page.wait_for_selector("#segList .seg")
            check(await page.locator(".tab.active").text_content() == "② ตรวจ transcript", "เปิดมาที่แท็บตรวจ transcript อัตโนมัติ")
            check(await page.locator("#segList .seg").count() == 3, "แสดง 3 ช่วงคำพูด")
            check(await page.locator("#tab-report").is_hidden(), "แท็บรายงานซ่อนอยู่")
            await page.screenshot(path=str(out / "2-review.png"))

            ta = page.locator("#segList .seg").nth(0).locator("textarea")
            await ta.fill("วันนี้เรามีเรื่องงบประมาณ ผมเสนอให้อนุมัติงบสองหมื่นบาท ครับ")
            await ta.blur()
            await page.wait_for_selector("#segList .seg .muted:has-text('แก้ไขแล้ว')")
            check(True, "แก้ข้อความแล้วบันทึกอัตโนมัติ และแสดงว่ามีต้นฉบับ")
            await page.locator("#segList .seg").nth(2).locator("button[data-act=delete]").click()
            await page.wait_for_selector("#segList .seg.deleted")
            check(await page.locator("#segList .seg.deleted").count() == 1, "ลบช่วงที่ไม่เกี่ยวข้องได้ (soft delete)")

            await page.fill("#segSearch", "alice")
            await page.wait_for_timeout(200)
            visible = await page.locator("#segList .seg:visible").count()
            check(visible == 1, f"ค้นหา 'alice' เหลือ 1 ช่วง (ได้ {visible})")
            await page.fill("#segSearch", "")

            check(await page.locator("button#verify").count() == 1, "มีปุ่มยืนยัน transcript")
            await page.click("#verify")
            await page.wait_for_selector("#tab-report:not([hidden])")
            check(any("ยังมี" in d and "ไม่ได้จับคู่" in d for d in dialogs) is False, "ไม่มีชื่อค้างจับคู่ (ช่วงของคนแปลกหน้าถูกลบแล้ว)")

            # ── 3. AI ร่างรายงาน ──
            print("3) AI ร่างรายงาน แก้ไข อนุมัติ")
            await page.wait_for_selector("#gen")
            check(await page.locator("#headerWarnsPlaceholder").count() == 0, "(ตรวจหน้ารายงานก่อนสร้าง)")
            await page.screenshot(path=str(out / "3-before-generate.png"))
            await page.click("#gen")
            await page.wait_for_selector("#approve", timeout=30000)
            check(await page.locator(".agenda").count() == 1, "มีวาระที่ AI ร่างให้ 1 วาระ")
            check(await page.locator("#actionList tr[data-i]").count() == 2, "มีงาน 2 รายการ")
            check("ฉบับร่าง" in await page.locator(".banner.warn").first.text_content(), "แสดงแบนเนอร์ฉบับร่าง")
            warn_text = await page.locator(".warnlist").first.text_content()
            check("ใครสักคน" in warn_text, "คำเตือนบอกว่าผู้รับผิดชอบ 'ใครสักคน' ไม่อยู่ในรายชื่อ")
            check(await page.locator(".flag").count() >= 1, "ช่องที่มีปัญหาถูกขีดกรอบเตือน")
            check(await page.locator(".evid .ok").count() >= 1, "แสดงเครื่องหมายว่าหลักฐานพบใน transcript")
            check(await page.locator(".evid .bad").count() >= 1, "แสดงเครื่องหมายเตือนหลักฐานที่ไม่พบใน transcript")
            await page.screenshot(path=str(out / "4-draft.png"), full_page=True)

            # แก้ไข: ผู้รับผิดชอบ + วันที่ + มติ แล้วบันทึกร่าง
            row2 = page.locator("#actionList tr[data-i='1']")
            await row2.locator("[data-k=assignee]").select_option("Bob")
            await row2.locator("[data-k=due_date]").fill("2026-10-12")
            await page.locator(".agenda [data-k=resolution]").fill("ที่ประชุมอนุมัติงบประมาณ 20,000 บาท")
            await page.click("#saveDraft")
            await page.wait_for_selector(".toast.ok:has-text('บันทึกร่างแล้ว')")
            check(True, "บันทึกร่างที่แก้แล้วสำเร็จ")
            warn_text = await page.locator(".warnlist").first.text_content()
            check("ใครสักคน" not in warn_text, "แก้ผู้รับผิดชอบแล้วคำเตือนเรื่องผู้รับผิดชอบหายไป")
            saved = db.get_latest_minutes(meeting_id)
            check(saved["content"]["agenda"][0]["resolution"] == "ที่ประชุมอนุมัติงบประมาณ 20,000 บาท", "ข้อความที่แก้ถูกบันทึกลง DB")
            check(saved["ai_content"]["agenda"][0]["resolution"] == "อนุมัติงบสองหมื่นบาท", "ฉบับ AI เดิมยังเก็บไว้ไม่ถูกทับ")

            # PDF ฉบับร่าง
            resp = await page.request.get(f"{base}/meetings/{meeting_id}/minutes.pdf")
            check(resp.status == 200 and (await resp.body()).startswith(b"%PDF"), "ดาวน์โหลด PDF ฉบับร่างได้")

            # อนุมัติ (ยังมีคำเตือน -> ต้องยืนยันผ่านกล่องข้อความ)
            dialogs.clear()
            await page.click("#approve")
            await page.wait_for_selector(".banner.ok:has-text('อนุมัติแล้วโดย')", timeout=15000)
            check(any("ควรตรวจก่อนอนุมัติ" in d for d in dialogs), "ระบบถามยืนยันเมื่อยังมีคำเตือนค้างก่อนอนุมัติ")
            check(await page.locator("#approve").count() == 0 and await page.locator("#revise").count() == 1,
                  "อนุมัติแล้วเหลือปุ่ม 'สร้างเวอร์ชันแก้ไข'")
            check(await page.locator("#r_summary").is_disabled(), "รายงานที่อนุมัติแล้วแก้ไม่ได้ (ช่องถูกล็อก)")
            check(await page.locator("#syncAll, #calConnect").count() == 1, "แสดงส่วนส่งเข้า Calendar หลังอนุมัติ")
            await page.screenshot(path=str(out / "5-approved.png"), full_page=True)

            resp = await page.request.get(f"{base}/meetings/{meeting_id}/minutes.pdf")
            check("draft" not in resp.headers.get("content-disposition", ""), "PDF หลังอนุมัติไม่ใช่ฉบับร่าง")

            # ย้อนไปดูแท็บอื่น ๆ ว่าล็อกถูกต้อง
            await page.click(".tab[data-tab=review]")
            await page.wait_for_selector("#segList .seg")
            check(await page.locator("#segList textarea:not([disabled])").count() == 0, "หลังอนุมัติ transcript ถูกล็อก")

            # ── 4. หน้าสดและประวัติ ──
            print("4) หน้าประชุมสดและประวัติ")
            await page.goto(base + "/")
            await page.click("#pastBtn")
            await page.wait_for_selector("#pastPanel a.pastRow")
            check("อนุมัติแล้ว" in await page.locator("#pastPanel a.pastRow").first.text_content(), "ประวัติแสดงสถานะ 'อนุมัติแล้ว'")
            await page.locator("#pastPanel a.pastRow").first.click()
            await page.wait_for_url(re.compile(r".*/meeting/\d+$"))
            check(True, "คลิกประวัติแล้วไปหน้าจัดการการประชุม")

            await browser.close()

        # "Failed to load resource" คือ Chrome บันทึกการตอบ 4xx ของ API — ในสคริปต์นี้เกิดจากขั้นตอนที่ตั้งใจให้ถูกปฏิเสธ
        # (ตั้งประธานซ้ำ = 400, กดอนุมัติทั้งที่ยังมีคำเตือน = 409) จึงข้าม แต่ JavaScript exception/error อื่นยังนับ
        real_errors = [e for e in errors if "favicon" not in e and "Failed to load resource" not in e]
        check(not real_errors, "ไม่มี error ใน console/JavaScript" + (f": {real_errors[:3]}" if real_errors else ""))
    finally:
        if server:
            server.should_exit = True
        for p in locals().get("patches", []):
            p.stop()
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
