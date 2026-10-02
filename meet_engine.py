"""
เครื่องยนต์กลาง: เข้าห้อง Google Meet, เปิดคำบรรยาย (CC), เฝ้าอ่านแล้วส่ง event ออกทาง callback

ใช้โดยบริการบอท (app.py)

event ที่ส่งออก (dict):
  {"type": "status",  "text": ...}
  {"type": "caption", "id": int, "name": str, "text": str, "final": bool}
  {"type": "error",   "text": ...}
  {"type": "ended"}
"""

import asyncio
import re

from playwright.async_api import async_playwright

MEET_PROFILE = ".meet_profile"

MEET_URL_RE = re.compile(r"^https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}(\?.*)?$", re.I)


JS_CAPTION_WATCHER = r"""
() => {
  if (window.__capWatcherInstalled) return;
  window.__capWatcherInstalled = true;
  window.__capSeq = window.__capSeq || 0;
  // heartbeat ให้ฝั่ง Python (watchdog) อ่านเช็กว่าตัวจับยังมีชีวิตและยังเห็นคำบรรยายอยู่ไหม
  window.__capLastActivity = Date.now();   // ครั้งล่าสุดที่ข้อความคำบรรยายเปลี่ยน
  window.__capRegionSeen = Date.now();     // ครั้งล่าสุดที่เห็นกรอบคำบรรยายบนจอ

  const FINALIZE_MS = 1500;
  const EMIT_THROTTLE_MS = 250;  // ส่ง event "ยังไม่ final" ได้ไม่เกินนี้ต่อแถว (กัน flood ผ่าน CDP/WebSocket)
  const lastRaw = new Map();   // row -> ข้อความดิบล่าสุดที่อ่านจาก DOM
  const commit = new Map();    // row -> ข้อความสะสม (ยาวขึ้นเรื่อย ๆ ไม่มีวันสั้นลง)
  const liveTail = new Map();  // row -> ส่วนท้ายของ commit ที่มาจาก "raw" ปัจจุบันจริง ๆ (ไม่ใช่ทั้งก้อน raw)
  const lastEmitAt = new Map();  // row -> เวลาที่ส่ง event ไม่ final ล่าสุด
  const timers = new Map();
  const emit = (p) => { try { window.__onCaption(p); } catch (e) {} };

  const findRegion = () =>
    [...document.querySelectorAll('.a4cQT')].find(el => el.offsetParent !== null) || null;

  const nameOf = (row) => {
    const el = row.querySelector('.KcIKyf, .zs7s8d, .jxFHg, .NWpY1d');
    if (el && el.innerText.trim()) return el.innerText.trim();
    const leaves = [...row.querySelectorAll('*')]
      .filter(e => e.children.length === 0 && e.textContent.trim())
      .map(e => e.textContent.trim());
    return leaves.sort((a, b) => a.length - b.length)[0] || '';
  };

  const textOf = (row, name) => {
    const el = row.querySelector('[jsname="tgaKEf"], .bh44bd, .VbkSUe, .iTTPOb');
    let t = ((el ? el.innerText : row.innerText) || '').trim();
    if (name && t.startsWith(name)) t = t.slice(name.length).trim();
    return t.replace(/\s+/g, ' ');
  };

  const rowsOf = (region) => {
    for (const sel of ['.nMcdL', '[class*="nMcdL"]']) {
      const r = region.querySelectorAll(sel);
      if (r.length) return [...r];
    }
    const out = new Set();
    region.querySelectorAll('img').forEach(img => {
      let r = img.parentElement;
      for (let i = 0; i < 5 && r && r !== region; i++) {
        if ((r.innerText || '').trim()) { out.add(r); break; }
        r = r.parentElement;
      }
    });
    return [...out];
  };

  // Google Meet จะเลื่อนตัดข้อความหน้า ๆ ของแถวคำบรรยายทิ้งเป็นช่วง ๆ ระหว่างที่คนพูดยาว ๆ
  // อยู่ (sliding window) ฟังก์ชันนี้ต่อข้อความใหม่เข้ากับส่วนที่สะสมไว้แล้ว โดยหาจุดที่
  // ท้ายข้อความเดิมทับซ้อนกับหน้าข้อความใหม่ยาวที่สุด แล้วต่อเฉพาะส่วนที่ไม่ซ้ำ กันข้อความ
  // ที่เคยจับได้แล้วหายไปตอน Meet ตัดหน้าทิ้ง
  // คืนทั้งข้อความที่ต่อแล้ว (next) และ "ส่วนที่ต่อเพิ่มจริง" (tail) — ต้องรู้ tail แยกจาก
  // incoming ทั้งก้อน เพราะรอบถัดไปถ้าข้อความยาวขึ้นตามปกติ เราต้องลบเฉพาะส่วนที่เคยต่อไว้จริง
  // ออกก่อน ไม่ใช่ลบทั้ง incoming (ไม่งั้นจะลบผิดขนาดแล้วต่อซ้ำข้อความเดิมเข้าไปอีกรอบ)
  const overlapAppend = (base, incoming) => {
    if (!base) return { next: incoming, tail: incoming };
    if (!incoming) return { next: base, tail: '' };
    // incoming เป็นเวอร์ชันที่ครอบ base อยู่แล้ว (เช่น ASR แก้คำเดิมเล็กน้อยแล้วโตขึ้น) -> ใช้ incoming ไปเลย
    if (incoming.includes(base)) return { next: incoming, tail: incoming };
    if (base.includes(incoming)) return { next: base, tail: '' };
    const max = Math.min(base.length, incoming.length);
    for (let k = max; k >= 3; k--) {
      if (base.slice(-k) === incoming.slice(0, k)) {
        const tail = incoming.slice(k);
        return { next: base + tail, tail };
      }
    }
    // หาจุดทับซ้อนไม่เจอเลย (ASR แก้ข้อความเดิมแบบไม่ใช่แค่ตัดหน้า) — เลือกข้อความที่ยาวกว่าแทน
    // การต่อกันตรง ๆ เพื่อไม่ให้คำซ้ำวนอยู่ในบรรทัดเดียวกัน (ยอมเสี่ยงหลุดคำเก่าดีกว่าคำซ้ำ)
    return incoming.length >= base.length
      ? { next: incoming, tail: incoming }
      : { next: base, tail: '' };
  };

  const updateCommit = (row, raw) => {
    const prevRaw = lastRaw.get(row) || '';
    const prevCommit = commit.get(row) || '';
    const prevTail = liveTail.get(row) || '';
    let next, tail;
    if (prevRaw && raw.startsWith(prevRaw)) {
      // ข้อความยาวขึ้นตามปกติ (ยังไม่ถูกตัดหน้า) -> ลบเฉพาะส่วนที่ต่อไว้จริงครั้งก่อน (prevTail,
      // ไม่ใช่ prevRaw ทั้งก้อน — ครั้งก่อนอาจเป็นแค่ tail จาก overlapAppend) แล้วต่อก้อนใหม่ทั้งก้อนแทน
      const base = prevCommit.endsWith(prevTail)
        ? prevCommit.slice(0, prevCommit.length - prevTail.length)
        : prevCommit;
      next = base + raw;
      tail = raw;
    } else {
      // Meet ตัดหน้า/รีเซ็ตข้อความในแถวนี้แล้ว -> ต่อเข้ากับของสะสมเดิมแทนการทับ
      ({ next, tail } = overlapAppend(prevCommit, raw));
    }
    lastRaw.set(row, raw);
    commit.set(row, next);
    liveTail.set(row, tail);
    return next;
  };

  const flush = (row, final) => {
    const n = nameOf(row) || 'ไม่ทราบชื่อ';
    const text = commit.get(row);
    if (text) emit({ id: row.__capId, name: n, text, final });
  };

  const cleanup = (row) => {
    clearTimeout(timers.get(row));
    timers.delete(row);
    lastRaw.delete(row);
    commit.delete(row);
    liveTail.delete(row);
    lastEmitAt.delete(row);
  };

  let liveRows = new Set();

  const scan = () => {
    const region = findRegion();
    if (!region) return;
    const rows = rowsOf(region);

    if (!rows.length) {
      const whole = (region.innerText || '').replace(/[ \t]+\n/g, '\n').trim();
      if (whole && lastRaw.get('__raw__') !== whole) {
        lastRaw.set('__raw__', whole);
        emit({ id: 0, name: '(raw)', text: whole.replace(/\n+/g, ' | '), final: true });
      }
      return;
    }

    const nowRows = new Set(rows);
    // แถวที่หายไปจากจอแล้ว (Meet เอาออกก่อนตัวจับเวลาจะทำงาน) -> ปิดจบด้วยข้อความสะสมทันที
    for (const row of liveRows) {
      if (!nowRows.has(row)) {
        flush(row, true);
        cleanup(row);
      }
    }
    liveRows = nowRows;

    for (const row of rows) {
      if (!row.__capId) row.__capId = (window.__capSeq += 1);
      const name = nameOf(row) || 'ไม่ทราบชื่อ';
      const raw = textOf(row, name);
      if (!raw || lastRaw.get(row) === raw) continue;

      const text = updateCommit(row, raw);
      window.__capLastActivity = Date.now();
      // ข้อความสะสมใน commit ครบเสมอ ข้ามการส่ง event ไม่ final ถี่ ๆ ได้โดยไม่ทำให้ข้อความหาย
      // (ตอน final จะส่งก้อนเต็มอีกครั้ง) — แค่ภาพสดบนจออัปเดตห่างขึ้นเล็กน้อย
      const now = Date.now();
      if (now - (lastEmitAt.get(row) || 0) >= EMIT_THROTTLE_MS) {
        lastEmitAt.set(row, now);
        emit({ id: row.__capId, name, text, final: false });
      }

      clearTimeout(timers.get(row));
      timers.set(row, setTimeout(() => {
        flush(row, true);
        timers.delete(row);
      }, FINALIZE_MS));
    }
  };

  let observed = null;
  let observer = null;

  // Meet สร้างกรอบคำบรรยายใหม่ได้ตอนเปลี่ยน layout — ต้อง disconnect observer ตัวเก่าทุกครั้ง
  // ไม่งั้นสะสมเรื่อย ๆ ตลอดการประชุมยาว ๆ (memory leak) และแถวของกรอบเก่าต้องปิดจบให้ครบก่อน
  const attach = (region) => {
    if (observer) observer.disconnect();
    for (const row of liveRows) {
      flush(row, true);
      cleanup(row);
    }
    liveRows = new Set();
    observed = region;
    observer = new MutationObserver(scan);
    observer.observe(region, { childList: true, subtree: true, characterData: true });
    scan();
  };

  const intervalId = setInterval(() => {
    const region = findRegion();
    if (region) window.__capRegionSeen = Date.now();
    if (region && region !== observed) attach(region);
  }, 2000);

  // ไว้ให้ test/harness ถอดตัวจับออกได้สะอาด
  window.__capWatcherStop = () => {
    clearInterval(intervalId);
    if (observer) observer.disconnect();
    for (const t of timers.values()) clearTimeout(t);
    timers.clear();
    window.__capWatcherInstalled = false;
  };
}
"""


WATCHDOG_INTERVAL_S = 15     # ตรวจสุขภาพบอททุกกี่วินาที
LEFT_ROOM_STRIKES = 3        # ไม่เจอปุ่มวางสายติดกันกี่รอบ ถึงถือว่าประชุมจบ/หลุดห้อง (3 x 15 = 45 วิ)
EVAL_FAIL_STRIKES = 5        # สั่ง JS ในหน้าไม่ได้ติดกันกี่รอบ ถึงถือว่าหน้าตาย
REGION_GONE_S = 60           # ไม่เห็นกรอบคำบรรยายนานเท่านี้ -> ลองเปิดคำบรรยายใหม่
SILENCE_WARN_S = 300         # ไม่มีข้อความคำบรรยายใหม่นานเท่านี้ -> เตือน (อาจแค่ไม่มีใครพูด)
SEQ_BASE_STEP = 1_000_000    # id แถวคำบรรยายของการติดตั้งตัวจับแต่ละรอบเริ่มห่างกันเท่านี้ (กัน id ชนกัน)


class _Stopped(Exception):
    pass


class _EngineLost(Exception):
    """บอทใช้งานต่อไม่ได้ (ประชุมจบ/เบราว์เซอร์ปิด/หน้า crash) — ต้องหยุดและปิดงานให้เรียบร้อย"""


class MeetCaptionEngine:
    def __init__(self, on_event, max_duration_s: float | None = None):
        self.on_event = on_event
        self.max_duration_s = max_duration_s   # None = ไม่จำกัด; ครบเวลาแล้วบอทออกจากห้องเอง
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._seq_base = 0

    # ── lifecycle ──
    def start(self, url: str) -> asyncio.Task:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(url))
        return self._task

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def wait(self):
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    async def stop(self):
        self._stop.set()
        t = self._task
        if not t:
            return
        for _ in range(20):
            if t.done():
                break
            await asyncio.sleep(0.25)
        if not t.done():
            t.cancel()
        try:
            # กันไว้เผื่อ context.close() ของ Chromium ค้าง (เช่น ยังถือ mic/camera
            # อยู่ระหว่างวางสาย) — ไม่ให้ปุ่มหยุดค้างทั้งแอปไปด้วย
            await asyncio.wait_for(asyncio.gather(t, return_exceptions=True), timeout=15)
        except asyncio.TimeoutError:
            self._emit(
                type="status",
                text="⚠️ หยุดเอนจินไม่ทันเวลา (เบราว์เซอร์อาจค้างอยู่เบื้องหลัง) — ปิดหน้าต่าง Chrome เองได้ถ้าจำเป็น",
            )

    # ── helpers ──
    def _emit(self, **ev):
        try:
            self.on_event(ev)
        except Exception:  # noqa: BLE001 — callback ต้องไม่ทำ engine ล้ม
            pass

    async def _guard(self, coro, timeout: float | None = None):
        """รอ coro แต่ถ้ามีคำสั่ง stop ระหว่างนั้นให้เลิก"""
        work = asyncio.ensure_future(coro)
        stopper = asyncio.ensure_future(self._stop.wait())
        done, pending = await asyncio.wait(
            {work, stopper}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
        if self._stop.is_set():
            work.cancel()
            raise _Stopped()
        if work in done:
            return work.result()
        work.cancel()
        raise asyncio.TimeoutError()

    async def _needs_login(self, page) -> bool:
        try:
            if "accounts.google.com" in page.url:
                return True
            return await page.locator('input[type="email"]').count() > 0
        except Exception:  # noqa: BLE001
            return False

    async def _enable_captions(self, page):
        cc = re.compile(r"คำบรรยายแทนเสียง|คำบรรยาย|captions?", re.I)
        off = re.compile(r"^\s*ปิด|turn off", re.I)

        try:
            buttons = page.get_by_role("button", name=cc)
            count = await buttons.count()
            for i in range(count):
                btn = buttons.nth(i)
                label = (await btn.get_attribute("aria-label")) or ""
                if "ตั้งค่า" in label or "settings" in label.lower():
                    continue
                if off.search(label):
                    self._emit(type="status", text=f"คำบรรยายเปิดอยู่แล้ว (ปุ่ม: {label!r})")
                    return
                if await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(1500)
                    self._emit(type="status", text=f"กดเปิดคำบรรยายแล้ว (ปุ่ม: {label!r})")
                    return
            self._emit(
                type="status",
                text=f"⚠️ หาปุ่มคำบรรยายไม่เจอ (เจอปุ่มที่ชื่อใกล้เคียง {count} ปุ่ม) — ลองกด shortcut แทน",
            )
        except Exception as e:  # noqa: BLE001
            self._emit(type="status", text=f"⚠️ หาปุ่มคำบรรยายไม่สำเร็จ: {e!r} — ลองกด shortcut แทน")

        try:
            await page.mouse.move(640, 700)
            await page.wait_for_timeout(300)
            await page.keyboard.press("c")
            await page.wait_for_timeout(1500)
            self._emit(type="status", text="กด shortcut 'c' เพื่อเปิดคำบรรยายแล้ว")
        except Exception as e:  # noqa: BLE001
            self._emit(type="status", text=f"⚠️ กด shortcut เปิดคำบรรยายไม่สำเร็จ: {e!r}")

    async def _install_watcher(self, page):
        # id แถวคำบรรยายต้องนับต่อจากรอบก่อนเสมอ: ถ้าหน้าถูกโหลดใหม่ window.__capSeq จะรีเซ็ตเป็น 0
        # แล้ว id ไปซ้ำกับแถวเก่า ฝั่งแอปจะเอาข้อความใหม่ไปทับ segment ของคนละคน
        self._seq_base += SEQ_BASE_STEP
        await page.evaluate(
            "(b) => { window.__capSeq = Math.max(window.__capSeq || 0, b); }", self._seq_base
        )
        await page.evaluate(JS_CAPTION_WATCHER)

    async def _monitor(self, page, context, joined):
        """เฝ้าสุขภาพบอทตลอดการประชุม — คืนค่าไม่ได้ ออกได้ทาง _Stopped (สั่งหยุด) หรือ _EngineLost เท่านั้น

        จับ: เบราว์เซอร์/หน้าถูกปิดหรือ crash, ประชุมจบหรือบอทหลุดห้อง, ตัวจับคำบรรยายหายไปหลังหน้า
        โหลดใหม่ (ติดตั้งซ้ำให้), กรอบคำบรรยายหาย (เปิดคำบรรยายใหม่), เงียบนาน (เตือน), ครบเวลาสูงสุด
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        lost = {}
        dead = asyncio.Event()

        def mark(reason: str):
            lost.setdefault("reason", reason)
            dead.set()

        page.on("close", lambda *_: mark("หน้าประชุมถูกปิด"))
        page.on("crash", lambda *_: mark("หน้าเบราว์เซอร์ crash"))
        context.on("close", lambda *_: mark("เบราว์เซอร์ถูกปิด"))

        gone = 0
        eval_fail = 0
        last_reenable = last_silence_warn = started

        while True:
            try:
                await self._guard(dead.wait(), timeout=WATCHDOG_INTERVAL_S)
                raise _EngineLost(lost["reason"])
            except asyncio.TimeoutError:
                pass  # ครบรอบตรวจปกติ

            if self.max_duration_s and loop.time() - started >= self.max_duration_s:
                raise _EngineLost(f"ครบเวลาสูงสุดที่ตั้งไว้ ({self.max_duration_s / 60:.0f} นาที)")

            try:
                # ใช้ count() ไม่ใช่ is_visible(): แถบปุ่มของ Meet ซ่อนตัวเองตอนไม่มีการขยับเมาส์ได้
                # แต่ปุ่มวางสายยังอยู่ใน DOM จนกว่าจะออกจากห้องจริง ๆ
                in_room = await joined.count() > 0
                region_gap_ms, quiet_ms, installed = await page.evaluate(
                    "() => [Date.now() - (window.__capRegionSeen || 0),"
                    " Date.now() - (window.__capLastActivity || 0), !!window.__capWatcherInstalled]"
                )
                eval_fail = 0
            except Exception:  # noqa: BLE001 — หน้ากำลังโหลดใหม่/ปิดอยู่
                eval_fail += 1
                if eval_fail >= EVAL_FAIL_STRIKES:
                    raise _EngineLost("สั่งงานหน้าประชุมไม่ได้ต่อเนื่อง (หน้าอาจค้างหรือถูกปิด)")
                continue

            if not in_room:
                gone += 1
                if gone >= LEFT_ROOM_STRIKES:
                    raise _EngineLost("ออกจากห้องประชุมแล้ว (ประชุมจบ หรือบอทถูกนำออกจากห้อง)")
                continue
            gone = 0

            if not installed:
                # หน้าถูกโหลดใหม่ (เช่น Meet รีเฟรชเอง) ตัวจับคำบรรยายหายไป
                self._emit(type="status", text="⚠️ ตัวจับคำบรรยายหายไป (หน้าถูกโหลดใหม่) — ติดตั้งใหม่")
                try:
                    await self._guard(self._enable_captions(page), timeout=60)
                    await self._install_watcher(page)
                except asyncio.TimeoutError:
                    pass
                continue

            now = loop.time()
            if region_gap_ms > REGION_GONE_S * 1000 and now - last_reenable >= REGION_GONE_S:
                last_reenable = now
                self._emit(
                    type="status",
                    text=f"⚠️ ไม่เห็นกรอบคำบรรยายมา {int(region_gap_ms / 1000)} วินาที — ลองเปิดคำบรรยายใหม่",
                )
                try:
                    await self._guard(self._enable_captions(page), timeout=60)
                except asyncio.TimeoutError:
                    pass
            elif quiet_ms > SILENCE_WARN_S * 1000 and now - last_silence_warn >= SILENCE_WARN_S:
                last_silence_warn = now
                self._emit(
                    type="status",
                    text=f"ไม่มีคำบรรยายใหม่มา {int(quiet_ms / 60000)} นาทีแล้ว (อาจไม่มีใครพูด)",
                )

    # ── main ──
    async def _run(self, url: str):
        try:
            async with async_playwright() as pw:
                self._emit(type="status", text="กำลังเปิดเบราว์เซอร์…")
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=MEET_PROFILE,
                    headless=False,
                    args=["--start-maximized", "--use-fake-ui-for-media-stream"],
                    no_viewport=True,
                    permissions=["microphone", "camera"],
                    locale="th-TH",
                )
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.expose_function(
                        "__onCaption", lambda p: self._emit(type="caption", **p)
                    )

                    self._emit(type="status", text="กำลังไปที่ห้องประชุม…")
                    await page.goto(url)
                    await page.wait_for_timeout(4000)

                    if await self._needs_login(page):
                        self._emit(
                            type="status",
                            text="ยังไม่ได้ล็อกอิน Google — ล็อกอินในหน้าต่าง Chrome ที่เปิดอยู่",
                        )
                        for _ in range(100):  # รอสูงสุด ~5 นาที
                            if self._stop.is_set():
                                raise _Stopped()
                            await asyncio.sleep(3)
                            if not await self._needs_login(page):
                                break
                        await page.goto(url)
                        await page.wait_for_timeout(4000)

                    # ปิดไมค์ + ปิดกล้อง
                    await page.keyboard.press("Control+d")
                    await page.keyboard.press("Control+e")

                    join_name = re.compile(
                        r"ขอเข้าร่วม|เข้าร่วมตอนนี้|เข้าร่วมเลย|ask to join|join now", re.I
                    )
                    try:
                        btn = page.get_by_role("button", name=join_name).first
                        await self._guard(btn.wait_for(state="visible", timeout=30000))
                        await btn.click()
                        self._emit(type="status", text="ส่งคำขอเข้าร่วมแล้ว — รอหัวหน้าห้องกดยอมรับ…")
                    except (_Stopped, asyncio.CancelledError):
                        raise
                    except Exception:  # noqa: BLE001
                        self._emit(type="status", text="ไม่พบปุ่มขอเข้าร่วม — อาจเข้าห้องอยู่แล้ว")

                    joined = page.locator(
                        '[aria-label*="วางสาย"], [aria-label*="ออกจาก"], '
                        '[aria-label*="leave" i], [aria-label*="hang up" i], [jsname="CQyl2b"]'
                    ).first
                    await self._guard(joined.wait_for(state="visible", timeout=300000))
                    self._emit(type="status", text="เข้าห้องแล้ว — กำลังเปิดคำบรรยาย")

                    await page.wait_for_timeout(1500)
                    await self._enable_captions(page)
                    await self._install_watcher(page)
                    self._emit(type="status", text="กำลังฟังคำบรรยายแบบเรียลไทม์")

                    await self._monitor(page, context, joined)
                finally:
                    try:
                        await asyncio.wait_for(context.close(), timeout=10)
                    except asyncio.TimeoutError:
                        self._emit(
                            type="status",
                            text="⚠️ ปิดเบราว์เซอร์ไม่ทันเวลา (ยังค้างอยู่เบื้องหลัง)",
                        )
                    except Exception as e:  # noqa: BLE001
                        self._emit(type="status", text=f"⚠️ ปิดเบราว์เซอร์ไม่สำเร็จ: {e!r}")
        except (_Stopped, asyncio.CancelledError):
            self._emit(type="status", text="หยุดแล้ว")
        except _EngineLost as e:
            self._emit(type="status", text=f"⚠️ บอทหยุดเอง: {e}")
        except Exception as e:  # noqa: BLE001
            self._emit(type="error", text=repr(e))
        finally:
            self._emit(type="ended")
