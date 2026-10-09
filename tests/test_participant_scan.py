"""อ่านรายชื่อคนในห้อง Meet (แผง "บุคคล") ด้วยหน้า Meet จำลองใน Chromium จริง

โครงสร้างหน้าจำลองคัดจากผลสคริปต์ tools/meet_people_probe.js ที่รันบนห้อง Meet จริง (ภาษาไทย, 9 ต.ค. 2569):
  - ปุ่มเปิดแผงเป็น <div role="button" jsname="ocqpFe"> ข้อความ "บุคคล" + จำนวนคน (ไม่ใช่ <button> และไม่มี aria-label)
  - รายชื่อเป็น role=list aria-label="ผู้เข้าร่วม" > role=listitem (aria-label = ชื่อ, มี data-participant-id)
เทสต์พิสูจน์ว่าโค้ดอ่าน/เปิดแผง/แจ้งผลถูกต้องตามโครงสร้างนี้ ส่วน Meet รุ่นอื่น/ภาษาอื่นยังไม่ได้ลองจริง ไม่ใช้ DB
"""

import asyncio
import unittest

from playwright.async_api import async_playwright

from bot import meet_engine


def _item(name: str, device: int, extra: str = "", sub: str = "") -> str:
    return (
        f'<div role="listitem" aria-label="{name}" data-participant-id="spaces/AbC/devices/{device}">'
        f'<div><span class="zWGUib">{name}</span>{extra}<div class="d93U2d">{sub}</div></div></div>'
    )


# แผงจริงของห้องที่มี 3 คน: ผู้จัดการประชุม (มี "(คุณ)" แยกเป็น span) + บอท + ผู้เข้าร่วมอีกคน
PANEL = (
    '<div role="list" aria-label="ผู้เข้าร่วม">'
    + _item("46 ธนาวีร์ บุญเกิด", 115, '<span class="NnTWjc">(คุณ)</span>', "ผู้จัดการประชุม")
    + _item("Meet Sync", 120)
    + _item("Thanawee BOONKERD", 119)
    + "</div>"
)
NAMES = ["46 ธนาวีร์ บุญเกิด", "Meet Sync", "Thanawee BOONKERD"]
WAITING = '<div role="list" aria-label="ผู้ที่กำลังรอเข้าร่วม">' + _item("คนที่ยังรออนุญาต", 130) + "</div>"
UNRELATED_LIST = '<div role="list" aria-label="Chat messages"><div role="listitem" aria-label="ข้อความแชท"></div></div>'
# ปุ่มจริง: div role=button ไม่มี aria-label; กดแล้วแผงจึงโผล่
REAL_BUTTON = (
    '<div role="button" jsname="ocqpFe"><span>บุคคล</span><span>3</span></div>'
    '<div id="host"></div>'
    '<script>document.querySelector("[jsname=ocqpFe]").addEventListener("click", '
    '() => { document.getElementById("host").innerHTML = window.PANEL; });</script>'
)


async def _page(pw, html: str, panel_html: str = PANEL):
    browser = await pw.chromium.launch()
    page = await browser.new_page()
    await page.set_content(html)
    await page.evaluate("(p) => { window.PANEL = p; }", panel_html)
    return browser, page


class ParticipantScanTests(unittest.TestCase):
    def run_scan(self, html: str, scans: int = 1, panel_html: str = PANEL):
        events = []

        async def go():
            async with async_playwright() as pw:
                browser, page = await _page(pw, html, panel_html)
                engine = meet_engine.MeetCaptionEngine(events.append)
                for _ in range(scans):
                    await engine._scan_participants(page)
                await browser.close()
        asyncio.run(go())
        return events

    def test_reads_names_from_the_real_meet_panel_structure(self):
        events = self.run_scan(UNRELATED_LIST + WAITING + '<div id="host">' + PANEL + "</div>")
        # ชื่อมาจาก aria-label (ไม่มี "(คุณ)" ปน) · รายการแชทและรายการ "กำลังรอเข้าร่วม" ไม่ปนเข้ามา
        self.assertEqual(events, [{"type": "participants", "names": NAMES}])

    def test_opens_the_panel_with_the_real_div_button_then_reads(self):
        events = self.run_scan(REAL_BUTTON)
        self.assertEqual([e["names"] for e in events], [NAMES])

    def test_falls_back_to_the_first_text_when_an_item_has_no_aria_label(self):
        panel = '<div role="list" aria-label="Participants"><div role="listitem"><div><span>ภาม</span></div><span>Presenting</span></div></div>'
        events = self.run_scan(panel)
        self.assertEqual([e["names"] for e in events], [["ภาม"]])

    def test_warns_once_when_no_panel_can_be_found_and_does_not_report_names(self):
        events = self.run_scan("<p>หน้าที่ไม่มีแผงผู้คน</p>", scans=5)
        self.assertEqual([e["type"] for e in events], ["status"])                          # เตือนครั้งเดียว ไม่ซ้ำทุกรอบ
        self.assertIn("ตั้ง", events[0]["text"])

    def test_the_warning_lists_what_the_bot_could_see_so_selectors_can_be_fixed(self):
        events = self.run_scan(UNRELATED_LIST + WAITING, scans=3)
        self.assertEqual([e["type"] for e in events], ["status"])
        self.assertIn("Chat messages", events[0]["text"])
        self.assertIn("ผู้ที่กำลังรอเข้าร่วม", events[0]["text"])

    def test_a_panel_that_will_not_open_is_left_closed_after_giving_up(self):
        # แผงไม่ตรงโครงสร้าง: กดปุ่มตามจำนวนครั้งที่กำหนดแล้วหยุด (ครั้งคู่ = แผงกลับมาปิดเหมือนเดิม ไม่กดสลับไปมาไม่จบ)
        out = []

        async def go():
            async with async_playwright() as pw:
                browser, page = await _page(
                    pw, '<div role="button" jsname="ocqpFe" onclick="window.n=(window.n||0)+1">บุคคล</div>')
                engine = meet_engine.MeetCaptionEngine(lambda e: None)
                for _ in range(6):
                    await engine._scan_participants(page)
                out.append(await page.evaluate("() => window.n"))
                await browser.close()
        asyncio.run(go())
        self.assertEqual(out[0], meet_engine.PEOPLE_OPEN_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
