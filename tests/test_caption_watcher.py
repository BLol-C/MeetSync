"""
ทดสอบตัวจับคำบรรยาย (JS_CAPTION_WATCHER) กับหน้า Meet จำลองใน Chromium จริง
พิสูจน์: ตอน Meet ตัดข้อความหน้าทิ้ง (sliding window) แล้วคนพูดยาวต่อ ข้อความสะสมต้องเท่ากับที่พูดจริง ไม่ซ้ำและไม่หาย
(บั๊กเดิม: ส่วนที่ทับซ้อนกับของสะสมถูกต่อซ้ำ ทำให้แถวเดียวมีข้อความเดิมวนหลายรอบ)
"""

import asyncio
import unittest

from playwright.async_api import async_playwright

from bot import meet_engine

WORDS = (
    "ก็ดั้งแต่ตอนที่เราบอกเม้าท์มอยกันจนถึงประมาณ ไม่รู้อะไรอื่นมากก็เห็นมีแค่แฟนแล้วก็มี F ที่มาใหม่กับ บ้าน เห็นอยู่กัน 3 คน "
    "มีแฟนมี F มีสร้าง มีแค่นี้ที่เห็นนะ ใช่ผมทักไปถามแล้วว่าเป็นไงบ้างเขาบอกดี เหมือน gf เอ่อ G เขียนว่า F ชื่อ ถาม"
).split(" ")

PAGE = (
    '<div class="a4cQT" style="width:300px;min-height:20px"><div class="nMcdL">'
    '<div class="KcIKyf">A</div><div jsname="tgaKEf"></div></div></div>'
)


def _frames(window_chars=None, revisions=None):
    """ลำดับข้อความที่ Meet แสดงในแถวเดียว: โตขึ้นทีละคำ, ตัดคำหน้าทิ้งเมื่อเกิน window_chars,
    และ revisions = {ขั้นที่: {ตำแหน่งคำ: คำใหม่}} คือ ASR แก้คำที่เคยแสดงไปแล้ว
    คืน (frames, ข้อความสุดท้ายที่ควรสะสมได้ = ทุกคำพร้อมคำที่ถูกแก้)"""
    words, frames = list(WORDS), []
    cur = []
    for step, w in enumerate(words):
        cur.append(w)
        for idx, new in (revisions or {}).get(step, {}).items():
            cur[idx] = new
        shown = list(cur)
        while window_chars and len(" ".join(shown)) > window_chars:
            shown.pop(0)
        frames.append(" ".join(shown))
    return frames, " ".join(cur)


async def _run(frames) -> str:
    events = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.expose_function("__onCaption", lambda e: events.append(e))
        await page.set_content(PAGE)
        await page.evaluate(meet_engine.JS_CAPTION_WATCHER)
        await page.wait_for_timeout(2500)   # ตัวจับผูกกับกรอบคำบรรยายทุก 2 วินาที
        for text in frames:
            await page.evaluate("(t) => document.querySelector('[jsname=tgaKEf]').textContent = t", text)
            await page.wait_for_timeout(100)
        await page.wait_for_timeout(2200)    # รอให้ปิดจบ (final)
        await browser.close()
    return [e for e in events if e["final"]][-1]["text"]


class CaptionWatcherTests(unittest.TestCase):
    def check(self, window_chars=None, revisions=None):
        frames, want = _frames(window_chars, revisions)
        self.assertEqual(asyncio.run(_run(frames)), want)

    def test_growing_text_is_kept_exactly(self):
        self.check()

    def test_sliding_window_trim_does_not_duplicate_or_lose_text(self):
        self.check(window_chars=120)

    def test_asr_revising_earlier_words_does_not_repeat_the_utterance(self):
        # บั๊กที่เจอจริงบน Meet: ASR แก้ "ดั้ง"->"ตั้ง" ต้นประโยคระหว่างที่ยังพูดต่อ -> ท่อนต้นถูกต่อซ้ำทุกรอบ
        self.check(revisions={12: {0: "ก็ตั้งแต่ตอนที่เราบอกเม้าท์มอยกันจนถึงประมาณ"}, 20: {5: "อ่ะตื่นมาก็เห็นมีแค่แฟนแล้วก็มี"}})

    def test_revision_inside_a_sliding_window(self):
        self.check(window_chars=120, revisions={25: {20: "ซ้ำ", 22: "คน"}})


if __name__ == "__main__":
    unittest.main()
