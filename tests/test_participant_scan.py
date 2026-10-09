"""อ่านรายชื่อคนในห้อง Meet (แผง "ผู้คน") ด้วยหน้า Meet จำลองใน Chromium จริง

สำคัญ: หน้าจำลองนี้เขียนตามโครงสร้างที่ "คาด" ไว้ (รายการ role=list ที่ aria-label เป็น Participants + role=listitem ต่อคน)
เทสต์จึงพิสูจน์ได้แค่ว่าโค้ดอ่าน/เปิดแผง/แจ้งผลถูกต้องตามโครงสร้างนี้ ไม่ได้พิสูจน์ว่า Meet จริงมีโครงสร้างตรงกัน
(ต้องซ้อมกับห้องจริง) ไม่ใช้ DB
"""

import asyncio
import unittest

from playwright.async_api import async_playwright

from bot import meet_engine

PANEL = (
    '<div role="list" aria-label="Participants">'
    '<div role="listitem" aria-label="ธนาวีร์ บุญเกิด (You)"><span>ธนาวีร์ บุญเกิด (You)</span><span>Meeting host</span></div>'
    '<div role="listitem"><div><span>ภาม</span></div><span>Presenting</span></div>'
    '<div role="listitem" aria-label="ศศิน"></div>'
    '</div>'
)
UNRELATED_LIST = '<div role="list" aria-label="Chat messages"><div role="listitem" aria-label="ข้อความแชท"></div></div>'
BUTTON_THAT_OPENS_PANEL = (
    '<button aria-label="Show everyone" onclick="document.getElementById(\'host\').innerHTML = window.PANEL">คน</button>'
    '<div id="host"></div>'
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

    def test_reads_names_from_an_open_panel(self):
        events = self.run_scan(UNRELATED_LIST + '<div id="host">' + PANEL + "</div>")
        self.assertEqual(events, [{"type": "participants",
                                   "names": ["ธนาวีร์ บุญเกิด (You)", "ภาม", "ศศิน"]}])   # ชื่ออยู่ใน aria-label หรือข้อความแรก; แชทไม่ปน

    def test_reads_the_thai_meet_panel_labels_seen_on_a_real_room(self):
        # จากภาพ Meet จริง: แผงชื่อ "บุคคล" กลุ่ม "ผู้มีส่วนร่วม" มีผู้จัดการประชุม + บอท + ผู้เข้าร่วมอีกคน
        thai = PANEL.replace('aria-label="Participants"', 'aria-label="ผู้มีส่วนร่วม"')
        events = self.run_scan("<div>" + thai + "</div>")
        self.assertEqual([e["names"] for e in events], [["ธนาวีร์ บุญเกิด (You)", "ภาม", "ศศิน"]])

    def test_the_warning_lists_what_the_bot_could_see_so_selectors_can_be_fixed(self):
        events = self.run_scan(UNRELATED_LIST + '<button aria-label="รายละเอียดผู้คน">x</button>', scans=3)
        self.assertEqual([e["type"] for e in events], ["status"])
        self.assertIn("Chat messages", events[0]["text"])
        self.assertIn("รายละเอียดผู้คน", events[0]["text"])

    def test_opens_the_panel_when_it_is_closed_then_reads(self):
        events = self.run_scan(BUTTON_THAT_OPENS_PANEL)
        self.assertEqual([e["names"] for e in events], [["ธนาวีร์ บุญเกิด (You)", "ภาม", "ศศิน"]])

    def test_warns_once_when_no_panel_can_be_found_and_does_not_report_names(self):
        events = self.run_scan("<p>หน้าที่ไม่มีแผงผู้คน</p>", scans=5)
        self.assertEqual([e["type"] for e in events], ["status"])                          # เตือนครั้งเดียว ไม่ซ้ำทุกรอบ
        self.assertIn("ตั้ง", events[0]["text"])

    def test_a_panel_that_will_not_open_is_left_closed_after_giving_up(self):
        # แผงไม่ตรงโครงสร้าง: กดปุ่มตามจำนวนครั้งที่กำหนดแล้วหยุด (ครั้งคู่ = แผงกลับมาปิดเหมือนเดิม ไม่กดสลับไปมาไม่จบ)
        clicks = []

        async def go():
            async with async_playwright() as pw:
                browser, page = await _page(
                    pw, '<button aria-label="Show everyone" onclick="window.n=(window.n||0)+1">คน</button>')
                engine = meet_engine.MeetCaptionEngine(clicks.append)
                for _ in range(6):
                    await engine._scan_participants(page)
                clicks.append({"clicks": await page.evaluate("() => window.n")})
                await browser.close()
        asyncio.run(go())
        self.assertEqual(clicks[-1]["clicks"], meet_engine.PEOPLE_OPEN_ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
