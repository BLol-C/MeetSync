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
import os
import re

from playwright.async_api import async_playwright

MEET_PROFILE = ".meet_profile"
# หน้าต่าง Chrome ของบอท: "hidden" (ค่าเริ่มต้น) = เปิดแล้วย่อเก็บไว้ ไม่เด้งขึ้นมา — อยากดูก็คลิกที่ taskbar (ทดสอบแล้วว่าย่อไว้ Playwright ไม่หน่วงหน้า)
# "visible" = เปิดเต็มจอเหมือนเดิม — ตั้งผ่านตัวแปร BOT_WINDOW ใน .env
BOT_WINDOW = os.environ.get("BOT_WINDOW", "hidden").strip().lower()
HIDDEN_POS = (-2400, -2400)

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

  // ต่อข้อความล่าสุดที่ Meet แสดงในแถว (raw) เข้ากับของสะสม (base) — Meet ทำได้ 3 อย่างกับข้อความในแถวระหว่างคนพูดยาว ๆ
  // ที่ทำให้ raw ไม่ใช่ "ของเดิม + ท้ายใหม่" เฉย ๆ: (1) ตัดคำหน้า ๆ ทิ้ง (sliding window) (2) ASR แก้คำที่เคยแสดงไปแล้ว
  // (3) ทั้งสองอย่างพร้อมกัน วิธีเดิม (เดาว่า raw ขึ้นต้นเหมือนครั้งก่อนหรือเปล่า) พลาดตอน (2)/(3) แล้วต่อ raw ซ้ำท้ายของสะสมทั้งก้อน
  // วิธีนี้หาว่า raw ทับซ้อนกับของสะสมตรงไหนด้วย "จุดยึด" (ข้อความสั้น ๆ ที่ตรงกันเป๊ะ) แล้วให้ raw ทับตั้งแต่จุดนั้นไปจนจบ
  // — ส่วนที่ ASR แก้ใหม่จึงได้ข้อความล่าสุดเสมอ และส่วนที่ Meet ตัดทิ้งไปแล้วยังอยู่ครบ ไม่มีทางต่อซ้ำเพราะ raw ไม่เคยถูกต่อท้ายตรง ๆ
  const ANCHOR = 8;
  const ANCHOR_SPAN = 400;   // หาจุดยึดจาก 400 ตัวอักษรแรกของ raw เท่านั้น — กันงานบวมเป็นกำลังสองเมื่อข้อความยาวเป็นหมื่นตัว
  const commonPrefix = (a, b) => {
    const n = Math.min(a.length, b.length);
    let i = 0;
    while (i < n && a[i] === b[i]) i++;
    return i;
  };
  const mergeCommit = (base, raw) => {
    if (!base) return raw;
    if (!raw) return base;
    if (raw.includes(base)) return raw;      // raw ครอบของสะสมทั้งหมด (โตขึ้น/แก้ท้าย) -> ใช้ raw
    if (base.includes(raw)) return base;     // raw เป็นแค่ช่วงหนึ่งของที่เคยจับไว้แล้ว
    if (raw.length >= ANCHOR) {
      // ลองจุดยึดจากหัว raw ก่อน แล้วค่อยขยับไปตามตัวอักษร (กรณีหัว raw เองก็ถูก ASR แก้ด้วย)
      for (let o = 0; o + ANCHOR <= raw.length && o <= ANCHOR_SPAN; o += 4) {
        const needle = raw.substr(o, ANCHOR);
        let best = -1, bestScore = 0;
        for (let pos = base.indexOf(needle); pos !== -1; pos = base.indexOf(needle, pos + 1)) {
          // ถ้าวลีเดียวกันโผล่หลายที่ เลือกที่ข้อความต่อจากนั้นตรงกับ raw ยาวที่สุด
          const score = commonPrefix(base.slice(pos), raw.slice(o));
          if (score >= bestScore) { best = pos; bestScore = score; }
        }
        if (best >= 0 && bestScore >= ANCHOR) return base.slice(0, Math.max(0, best - o)) + raw;
      }
    }
    // หาจุดทับซ้อนไม่เจอเลย (ข้อความใหม่ทั้งก้อน/ถูกแก้เกือบหมด) — เลือกที่ยาวกว่า ไม่ต่อกันตรง ๆ
    // เพื่อไม่ให้คำซ้ำวนอยู่ในบรรทัดเดียว (ยอมเสี่ยงหลุดคำเก่าดีกว่าคำซ้ำ)
    return raw.length >= base.length ? raw : base;
  };

  const updateCommit = (row, raw) => {
    const next = mergeCommit(commit.get(row) || '', raw);
    lastRaw.set(row, raw);
    commit.set(row, next);
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


# อ่านรายชื่อคนในห้องจากแผง "บุคคล" ของ Meet — ไว้ตั้ง "เข้าร่วม" ให้คนที่มาแต่ไม่ได้พูด
# โครงสร้างรายการ (role=list "ผู้เข้าร่วม" > role=listitem) และปุ่ม div[jsname=ocqpFe] ยืนยันจากผลสคริปต์ tools/meet_people_probe.js
# ที่รันบนห้อง Meet ภาษาไทยจริงเมื่อ 9 ต.ค. 2569 — แต่บอท "กดเปิดแผงเอง" ด้วยปุ่มนี้ยังไม่ได้ทดสอบกับห้องจริง
# (jsname เป็นชื่อภายในของ Meet เปลี่ยนได้โดยไม่แจ้ง) ถ้าอ่านไม่ได้ ระบบแจ้งในสถานะบอทและไม่กระทบการจับคำบรรยาย
JS_READ_PARTICIPANTS = """
() => {
  // โครงสร้างที่เห็นจาก Meet จริง (ห้องภาษาไทย): role=list ชื่อ "ผู้เข้าร่วม" > role=listitem (aria-label = ชื่อ, มี data-participant-id)
  const labelRe = /participants|people|everyone|contributors|ผู้เข้าร่วม|ผู้คน|ทุกคน|บุคคล|ผู้มีส่วนร่วม|ผู้ร่วมประชุม/i;
  const waitingRe = /waiting|request|admit|รอ|ขอเข้า/i;   // รายการคนที่ยังรออนุญาตเข้าห้อง ไม่ใช่คนที่อยู่ในห้อง
  const all = [...document.querySelectorAll('[role="list"]')];
  const lists = all.filter((l) => {
    const label = l.getAttribute('aria-label') || '';
    return labelRe.test(label) && !waitingRe.test(label);
  });
  if (!lists.length) {
    // ไม่พบแผง: ส่งสิ่งที่เห็นในหน้ากลับไปด้วย เพื่อให้ปรับ selector ตาม Meet จริงได้ (ไม่ใช่การเดา)
    const buttons = [...document.querySelectorAll('button[aria-label], [role="button"][jsname]')]
      .map((b) => b.getAttribute('aria-label') || ('jsname=' + b.getAttribute('jsname') + ' ' + (b.textContent || '').trim().slice(0, 20)))
      .filter((t) => labelRe.test(t) || t.includes('ocqpFe')).slice(0, 6);
    return { panel: false, names: [], lists: all.map((l) => l.getAttribute('aria-label') || '(ไม่มีชื่อ)').slice(0, 8), buttons };
  }
  const names = [];
  for (const list of lists) {
    for (const item of list.querySelectorAll('[role="listitem"]')) {
      let name = (item.getAttribute('aria-label') || '').trim();
      if (!name) {
        const leaves = [...item.querySelectorAll('span, div')].filter((e) => !e.children.length)
          .map((e) => (e.textContent || '').trim()).filter((t) => t);
        name = leaves[0] || '';
      }
      if (name && name.length <= 100 && !names.includes(name)) names.push(name);
    }
  }
  return { panel: true, names: names.slice(0, 300) };
}
"""
PEOPLE_BUTTON = (
    '[role="button"][jsname="ocqpFe"], '    # ปุ่ม "บุคคล" ที่เห็นจาก Meet จริง (div ไม่ใช่ <button> และไม่มี aria-label)
    'button[aria-label*="people" i], button[aria-label*="everyone" i], button[aria-label*="participants" i], '
    'button[aria-label*="ผู้คน"], button[aria-label*="ทุกคน"], button[aria-label*="ผู้เข้าร่วม"], '
    'button[aria-label*="บุคคล"], button[aria-label*="ผู้มีส่วนร่วม"], button[aria-label*="ผู้ร่วมประชุม"]'
)
PARTICIPANT_SCAN_S = int(os.environ.get("BOT_PARTICIPANT_SCAN_S", "60"))   # อ่านรายชื่อคนในห้องทุกกี่วินาที (0 = ปิด)
PEOPLE_OPEN_ATTEMPTS = 2   # กดปุ่มผู้คนเพื่อเปิดแผงได้ติดกันกี่ครั้งก่อนยอมแพ้ (ยอมแพ้ที่รอบคู่ = แผงกลับมาปิดเหมือนเดิม)


WATCHDOG_INTERVAL_S = 15     # ตรวจสุขภาพบอททุกกี่วินาที
LEFT_ROOM_STRIKES = 3        # ไม่เจอปุ่มวางสายติดกันกี่รอบ ถึงถือว่าประชุมจบ/หลุดห้อง (3 x 15 = 45 วิ)
EVAL_FAIL_STRIKES = 5        # สั่ง JS ในหน้าไม่ได้ติดกันกี่รอบ ถึงถือว่าหน้าตาย
REGION_GONE_S = 60           # ไม่เห็นกรอบคำบรรยายนานเท่านี้ -> ลองเปิดคำบรรยายใหม่
SILENCE_WARN_S = 300         # ไม่มีข้อความคำบรรยายใหม่นานเท่านี้ -> เตือน (อาจแค่ไม่มีใครพูด)
JOIN_WAIT_S = 1200            # รอ host กดอนุญาตเข้าห้องได้นานสุดเท่านี้
CAPTION_WAIT_S = 1200         # รอจนเปิดคำบรรยายสำเร็จได้นานสุดเท่านี้ (ครอบช่วงรอ host)
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
        self._people_attempts = 0
        self._people_warned = False

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

    async def _caption_state(self, page):
        """ดูสถานะคำบรรยายจากหน้าจอจริง — คืน (สถานะ, ปุ่ม): 'on' | 'off' (มีปุ่มเปิดให้กด) | 'unknown' (ไม่เจอปุ่มเลย)"""
        try:
            if await page.evaluate(
                "() => [...document.querySelectorAll('.a4cQT')].some(el => el.offsetParent !== null)"
            ):
                return "on", None
        except Exception:  # noqa: BLE001
            pass
        cc = re.compile(r"คำบรรยายแทนเสียง|คำบรรยาย|captions?", re.I)
        off = re.compile(r"^\s*ปิด|turn off", re.I)
        try:
            buttons = page.get_by_role("button", name=cc)
            first_off = None
            for i in range(await buttons.count()):
                btn = buttons.nth(i)
                label = (await btn.get_attribute("aria-label")) or ""
                if "ตั้งค่า" in label or "settings" in label.lower():
                    continue
                if off.search(label) or (await btn.get_attribute("aria-pressed")) == "true":
                    return "on", None
                if first_off is None:
                    first_off = btn
            if first_off is not None:
                return "off", first_off
        except Exception:  # noqa: BLE001
            pass
        return "unknown", None

    async def _enable_captions(self, page, attempts: int = 3) -> bool:
        """พยายามเปิดคำบรรยายแล้ว "ตรวจซ้ำจากหน้าจอ" ว่าเปิดจริง — คืน True เฉพาะเมื่อยืนยันได้เท่านั้น
        (ห้ามรายงานสำเร็จจากการแค่กดปุ่ม: ตอนยังอยู่ห้องรอ/ปุ่มซ่อนอยู่ การกดไม่มีผล)"""
        for attempt in range(1, attempts + 1):
            state, btn = await self._caption_state(page)
            if state == "on":
                return True
            try:
                # แถบปุ่มของ Meet ซ่อนตัวเองเมื่อไม่ขยับเมาส์ — ขยับให้โผล่ก่อนกด
                await page.mouse.move(640, 650)
                await page.mouse.move(660, 700)
                await page.wait_for_timeout(500)
                if state == "off":
                    await btn.click(timeout=5000)
                elif attempt >= 2:
                    # ไม่เจอปุ่มเลย (เช่น ซ่อนในเมนู ⋮) — ลอง shortcut; ถ้าที่จริงเปิดอยู่แล้วรอบถัดไปจะเห็นและกดกลับให้
                    await page.keyboard.press("c")
            except Exception as e:  # noqa: BLE001
                self._emit(type="status", text=f"⚠️ กดเปิดคำบรรยายไม่สำเร็จ (รอบ {attempt}): {e!r}")
            await page.wait_for_timeout(1500)
        state, _ = await self._caption_state(page)
        return state == "on"

    async def _ensure_captions(self, page, wait_s: float = CAPTION_WAIT_S) -> bool:
        """วนเปิดคำบรรยายจนยืนยันได้จริง (หรือหมดเวลา) — ครอบกรณี host ยังไม่กดอนุญาต: ยังไม่มีปุ่มคำบรรยายก็รอต่อ"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_s
        tries = 0
        while True:
            if await self._guard(self._enable_captions(page), timeout=60):
                self._emit(type="status", text="✅ เข้าห้องแล้วและเปิดคำบรรยายแล้ว (ตรวจยืนยันจากหน้าจอ)")
                return True
            tries += 1
            if loop.time() >= deadline:
                return False
            if tries % 5 == 1:
                self._emit(
                    type="status",
                    text="⏳ ยังไม่ได้เข้าห้อง/ยังเปิดคำบรรยายไม่ได้ — ถ้า host ยังไม่กดยอมรับบอท ให้กดยอมรับ (บอทจะลองใหม่เอง)",
                )
            await self._guard(asyncio.sleep(3))

    async def _place_window(self, page, visible: bool):
        """visible=True: แสดงหน้าต่างบอทบนจอ / False: ย่อเก็บไว้ (คลิกที่ taskbar แล้วขึ้นมาที่ตำแหน่งปกติ)
        ย่อ ไม่ใช่ซ่อนนอกจอ เพราะหน้าต่างนอกจอผู้ใช้เรียกกลับมาเองไม่ได้ — พลาดได้ ไม่กระทบการทำงาน"""
        try:
            cdp = await page.context.new_cdp_session(page)
            win = await cdp.send("Browser.getWindowForTarget")
            wid = win["windowId"]
            # ต้องย้ายเข้าจอขณะยังเป็นหน้าต่างปกติก่อน (ย้ายตอนย่ออยู่ไม่ได้) แล้วค่อยย่อทันที — ตอนเปิดบอทหน้าต่างอยู่นอกจอ จึงไม่มีอะไรเด้ง
            # เต็มจอ (maximized) = ขนาดพอดีกับจอของเครื่องนี้เสมอ และตอนผู้ใช้คลิก taskbar คืนหน้าต่าง Chrome จะกลับมาเป็นเต็มจอเหมือนเดิม
            await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"left": 0, "top": 0, "windowState": "normal"}})
            await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "maximized"}})
            if not visible:
                await cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
            await cdp.detach()
        except Exception as e:  # noqa: BLE001
            self._emit(type="status", text=f"⚠️ ย้ายหน้าต่างบอทไม่สำเร็จ: {e!r}")

    async def _install_watcher(self, page):
        # id แถวคำบรรยายต้องนับต่อจากรอบก่อนเสมอ: ถ้าหน้าถูกโหลดใหม่ window.__capSeq จะรีเซ็ตเป็น 0
        # แล้ว id ไปซ้ำกับแถวเก่า ฝั่งแอปจะเอาข้อความใหม่ไปทับ segment ของคนละคน
        self._seq_base += SEQ_BASE_STEP
        await page.evaluate(
            "(b) => { window.__capSeq = Math.max(window.__capSeq || 0, b); }", self._seq_base
        )
        await page.evaluate(JS_CAPTION_WATCHER)

    async def _scan_participants(self, page):
        """อ่านรายชื่อคนในห้อง (เปิดแผงผู้คนให้ถ้ายังไม่เปิด) แล้วส่ง event "participants" — อ่านไม่ได้ก็แค่แจ้งครั้งเดียว"""
        result = await page.evaluate(JS_READ_PARTICIPANTS)
        if not result["panel"] and self._people_attempts < PEOPLE_OPEN_ATTEMPTS:
            self._people_attempts += 1
            button = page.locator(PEOPLE_BUTTON).first
            if await button.count():
                await button.click(timeout=3000)
                await page.wait_for_timeout(1000)
                result = await page.evaluate(JS_READ_PARTICIPANTS)
        if result["panel"]:
            self._people_attempts = 0
            self._people_warned = False
            self._emit(type="participants", names=result["names"])
        elif self._people_attempts >= PEOPLE_OPEN_ATTEMPTS and not self._people_warned:
            self._people_warned = True
            seen = (f" [บอทเห็นรายการ: {result.get('lists') or '-'} · ปุ่ม: {result.get('buttons') or '-'}]")[:300]
            self._emit(type="status", text="⚠️ อ่านรายชื่อผู้เข้าร่วมในห้องไม่ได้ (ไม่พบแผงผู้คนของ Meet) — "
                                           "คนที่มาฟังเฉยๆ ต้องตั้ง \"เข้าร่วม\" เองที่แท็บ ①" + seen)

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
        last_reenable = last_silence_warn = last_scan = started

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
                    await self._guard(self._ensure_captions(page, 120), timeout=150)
                    await self._install_watcher(page)
                except asyncio.TimeoutError:
                    pass
                continue

            now = loop.time()
            if PARTICIPANT_SCAN_S and now - last_scan >= PARTICIPANT_SCAN_S:
                last_scan = now
                try:
                    await self._guard(self._scan_participants(page), timeout=20)
                except asyncio.TimeoutError:
                    pass
                except _Stopped:
                    raise
                except Exception:  # noqa: BLE001 — อ่านรายชื่อพลาดต้องไม่กระทบการจับคำบรรยาย
                    pass
            if region_gap_ms > REGION_GONE_S * 1000 and now - last_reenable >= REGION_GONE_S:
                last_reenable = now
                self._emit(
                    type="status",
                    text=f"⚠️ ไม่เห็นกรอบคำบรรยายมา {int(region_gap_ms / 1000)} วินาที — ลองเปิดคำบรรยายใหม่",
                )
                try:
                    if await self._guard(self._enable_captions(page), timeout=60):
                        self._emit(type="status", text="✅ เปิดคำบรรยายแล้ว (ตรวจยืนยันจากหน้าจอ)")
                    else:
                        self._emit(type="status", text="⚠️ ยังเปิดคำบรรยายไม่สำเร็จ — จะลองใหม่รอบหน้า")
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
                hidden = BOT_WINDOW != "visible"
                launch_args = ["--use-fake-ui-for-media-stream"]
                launch_args += (
                    [f"--window-position={HIDDEN_POS[0]},{HIDDEN_POS[1]}", "--window-size=1280,900"]
                    if hidden else ["--start-maximized"]
                )
                self._emit(type="status", text="กำลังเปิดเบราว์เซอร์…" + (" (ย่อไว้ที่ taskbar — คลิกเพื่อดูได้)" if hidden else ""))
                context = await pw.chromium.launch_persistent_context(
                    user_data_dir=MEET_PROFILE,
                    headless=False,
                    args=launch_args,
                    no_viewport=True,
                    permissions=["microphone", "camera"],
                    locale="th-TH",
                )
                try:
                    page = context.pages[0] if context.pages else await context.new_page()
                    if hidden:
                        await self._place_window(page, False)   # หน้าต่างเกิดนอกจอ -> ย้ายเข้าจอแล้วย่อเก็บทันที
                    await page.expose_function(
                        "__onCaption", lambda p: self._emit(type="caption", **p)
                    )

                    self._emit(type="status", text="กำลังไปที่ห้องประชุม…")
                    await page.goto(url)
                    await page.wait_for_timeout(4000)

                    if await self._needs_login(page):
                        if hidden:
                            await self._place_window(page, True)
                        self._emit(
                            type="status",
                            text="ยังไม่ได้ล็อกอิน Google — ล็อกอินในหน้าต่าง Chrome ที่เปิดขึ้นมา",
                        )
                        for _ in range(100):  # รอสูงสุด ~5 นาที
                            if self._stop.is_set():
                                raise _Stopped()
                            await asyncio.sleep(3)
                            if not await self._needs_login(page):
                                break
                        if hidden:
                            await self._place_window(page, False)
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
                        label = ((await btn.get_attribute("aria-label")) or (await btn.inner_text()) or "").strip()
                        await btn.click()
                        if re.search(r"ขอเข้าร่วม|ask to join", label, re.I):
                            self._emit(type="status", text="ส่งคำขอเข้าร่วมแล้ว — รอหัวหน้าห้องกดยอมรับ…")
                        else:
                            # ปุ่ม "เข้าร่วมตอนนี้" = เข้าได้เลย (เช่น เคยได้รับอนุญาตแล้ว) ไม่ต้องรอ host
                            self._emit(type="status", text="กดเข้าร่วมแล้ว (ไม่ต้องรอ host) — กำลังเข้าห้อง…")
                    except (_Stopped, asyncio.CancelledError):
                        raise
                    except Exception:  # noqa: BLE001
                        self._emit(type="status", text="ไม่พบปุ่มขอเข้าร่วม — อาจเข้าห้องอยู่แล้ว")

                    joined = page.locator(
                        '[aria-label*="วางสาย"], [aria-label*="ออกจาก"], '
                        '[aria-label*="leave" i], [aria-label*="hang up" i], [jsname="CQyl2b"]'
                    ).first
                    await self._guard(joined.wait_for(state="visible", timeout=JOIN_WAIT_S * 1000))
                    self._emit(type="status", text="กำลังตรวจว่าเข้าห้องได้แล้วและเปิดคำบรรยาย…")

                    await page.wait_for_timeout(1500)
                    ok = await self._ensure_captions(page)
                    await self._install_watcher(page)
                    if ok:
                        self._emit(type="status", text="กำลังฟังคำบรรยายแบบเรียลไทม์")
                    else:
                        self._emit(
                            type="status",
                            text="⚠️ เปิดคำบรรยายไม่สำเร็จ — ยังไม่ได้ฟังอะไร เปิด CC เองในหน้าต่าง Chrome ของบอท (ปุ่ม CC ด้านล่าง)",
                        )

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
