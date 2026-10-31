"""ปุ่มเข้าห้องในหน้ารอก่อนเข้า Meet (จำลองด้วย Chromium จริง) — รวมหน้า "เปลี่ยนที่นี่" ที่ขึ้นเมื่อบัญชีบอทยังค้างอยู่ในห้องจากหน้าต่างเดิม

ข้อความปุ่มคัดจากภาพหน้าจอ Meet จริง (31 ต.ค. 2569) เทสต์พิสูจน์แค่ว่าบอทหาปุ่มเจอและเลือกถูกปุ่ม ไม่ได้ลองกดกับ Meet จริง
"""

import asyncio
import unittest

from playwright.async_api import async_playwright

from bot import meet_engine


async def _find(html: str) -> str | None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.set_content(html)
        btn = page.get_by_role("button", name=meet_engine.JOIN_BUTTON_RE).first
        try:
            await btn.wait_for(state="visible", timeout=1500)
            label = (await btn.inner_text()).strip()
        except Exception:  # noqa: BLE001
            label = None
        await browser.close()
    return label


class JoinButtonTests(unittest.TestCase):
    def find(self, *labels: str):
        return asyncio.run(_find("".join(f"<button>{t}</button>" for t in labels)))

    def test_switch_here_page_is_clicked_instead_of_waiting_forever(self):
        # หน้าที่เห็นจริง: ปุ่ม "เปลี่ยนที่นี่" + ลิงก์ "เข้าร่วมที่นี่ด้วย" — ต้องเลือก "เปลี่ยนที่นี่" ไม่ใช่ปล่อยค้าง
        self.assertEqual(self.find("เปลี่ยนที่นี่", "เข้าร่วมที่นี่ด้วย", "นำเสนอ"), "เปลี่ยนที่นี่")
        self.assertEqual(self.find("Switch here", "Join here too"), "Switch here")

    def test_usual_join_buttons_still_found(self):
        self.assertEqual(self.find("ขอเข้าร่วม"), "ขอเข้าร่วม")
        self.assertEqual(self.find("เข้าร่วมตอนนี้"), "เข้าร่วมตอนนี้")

    def test_labels_are_told_apart(self):
        self.assertTrue(meet_engine.SWITCH_BUTTON_RE.search("เปลี่ยนที่นี่"))
        self.assertFalse(meet_engine.SWITCH_BUTTON_RE.search("เข้าร่วมตอนนี้"))
        self.assertFalse(meet_engine.SWITCH_BUTTON_RE.search("ขอเข้าร่วม"))


if __name__ == "__main__":
    unittest.main()
