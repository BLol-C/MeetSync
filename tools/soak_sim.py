"""
ตัวจำลอง Google Meet สำหรับทดสอบตัวจับคำบรรยายและ watchdog โดยไม่ต้องมีห้องประชุมจริง

รันจากโฟลเดอร์โปรเจกต์:
  venv\\Scripts\\python.exe tools\\soak_sim.py soak --minutes 5      # ทดสอบความถูกต้อง + หน่วยความจำ
  venv\\Scripts\\python.exe tools\\soak_sim.py lifecycle             # ทดสอบ watchdog (ประชุมจบ/หน้าปิด/โหลดใหม่)

โหมด soak: DOM ปลอมที่คนพูดต่อเนื่อง ตัดหัวข้อความแบบ sliding window ของ Meet และสร้างกรอบคำบรรยายใหม่
เป็นระยะ -> เทียบข้อความ final ที่ตัวจับได้กับสิ่งที่ "พูดจริง" ทุกคำ และวัด JS heap / DOM nodes / event
listeners ผ่าน Chrome DevTools (หลัง GC) ว่าโตขึ้นเรื่อย ๆ หรือไม่

หมายเหตุ: นี่พิสูจน์ตรรกะของตัวจับและ watchdog เท่านั้น ไม่แทนการทดสอบกับ Meet จริงนาน 90 นาที
(ดู tools\\proc_monitor.ps1 สำหรับวัดหน่วยความจำของ python/chrome ตอนรันจริง)
"""

import argparse
import asyncio
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright  # noqa: E402

from bot import meet_engine as me  # noqa: E402

FAKE_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button aria-label="Leave call" id="leave">leave</button>
<script>
const NAMES = ['สมชาย', 'Alice', 'Bob'];
const MAX_VISIBLE = 180;           // Meet ตัดหัวข้อความเมื่อยาวเกินประมาณนี้ (sliding window)
let wordNo = 0, region = null, cur = null, pauseUntil = 0, lastRegionAt = Date.now();
window.__truth = [];
window.__regionEveryMs = 45000;
window.__speed = 100;              // ms ต่อคำ

function mkRegion() {
  const d = document.createElement('div');
  d.className = 'a4cQT';
  // ห้ามใช้ position:fixed: offsetParent ของ element แบบ fixed เป็น null ตัวจับจะมองว่ากรอบไม่แสดงอยู่
  d.style.cssText = 'position:absolute;bottom:0;left:0;width:600px;min-height:20px';
  document.body.appendChild(d);
  return d;
}

function tick() {
  const now = Date.now();
  if (!region) region = mkRegion();
  if (!cur && now >= pauseUntil) {
    if (now - lastRegionAt > window.__regionEveryMs) {   // Meet สร้างกรอบใหม่ตอน layout เปลี่ยน
      region.remove(); region = mkRegion(); lastRegionAt = now;
    }
    const row = document.createElement('div');
    row.className = 'nMcdL';
    row.innerHTML = '<div class="KcIKyf"></div><div jsname="tgaKEf"></div>';
    const name = NAMES[Math.floor(Math.random() * NAMES.length)];
    row.querySelector('.KcIKyf').textContent = name;
    region.appendChild(row);
    cur = { row, name, words: [], target: 15 + Math.floor(Math.random() * 45) };
  }
  if (cur) {
    cur.words.push('w' + (++wordNo));
    let visible = cur.words.slice();
    while (visible.join(' ').length > MAX_VISIBLE && visible.length > 1) visible.shift();
    cur.row.querySelector('[jsname="tgaKEf"]').textContent = visible.join(' ');
    if (cur.words.length >= cur.target) {
      const done = cur; cur = null;
      pauseUntil = now + 500 + Math.random() * 1500;
      // รอให้ตัวจับกำหนด id ให้แถวก่อนค่อยบันทึกเฉลย แล้วค่อยเอาแถวออกจากจอ (Meet ลบแถวเก่าทิ้งเอง)
      setTimeout(() => {
        window.__truth.push({ id: done.row.__capId || null, name: done.name, text: done.words.join(' ') });
        done.row.remove();
      }, 2500);
    }
  }
}
setInterval(tick, window.__speed);
</script></body></html>
"""


def fmt_mb(b):
    return f"{b / 1024 / 1024:.1f}"


async def cdp_metrics(client):
    await client.send("HeapProfiler.collectGarbage")
    res = await client.send("Performance.getMetrics")
    return {m["name"]: m["value"] for m in res["metrics"]}


async def run_soak(minutes: float):
    events = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.expose_function("__onCaption", lambda p: events.append(p))
        html = pathlib.Path(tempfile.mkdtemp()) / "fake_meet.html"
        html.write_text(FAKE_PAGE, encoding="utf-8")
        await page.goto(html.as_uri())
        client = await page.context.new_cdp_session(page)
        await client.send("Performance.enable")
        await page.evaluate(me.JS_CAPTION_WATCHER)

        samples = []
        t0 = time.time()
        end = t0 + minutes * 60
        print(f"จำลองการประชุม {minutes:g} นาที (สุ่มวัดทุก 15 วินาที)")
        print(f"{'เวลา(s)':>8} {'heapMB':>8} {'nodes':>8} {'listeners':>10} {'events':>8} {'utterances':>11}")
        while time.time() < end:
            await asyncio.sleep(15)
            m = await cdp_metrics(client)
            n_truth = await page.evaluate("window.__truth.length")
            samples.append((m["JSHeapUsedSize"], m["Nodes"], m["JSEventListeners"]))
            print(f"{time.time() - t0:8.0f} {fmt_mb(m['JSHeapUsedSize']):>8} {int(m['Nodes']):>8} "
                  f"{int(m['JSEventListeners']):>10} {len(events):>8} {n_truth:>11}")

        await asyncio.sleep(5)  # ให้ประโยคสุดท้ายจบ + ถูก finalize
        truth = await page.evaluate("window.__truth")
        await browser.close()

    # ── ความถูกต้อง: ข้อความ final สุดท้ายของแต่ละแถวต้องเท่ากับที่พูดจริงทุกคำ ──
    last_final = {}
    for ev in events:
        if ev.get("final"):
            last_final[ev["id"]] = ev
    bad, missing = [], 0
    for t in truth:
        got = last_final.get(t["id"])
        if got is None:
            missing += 1
        elif got["text"] != t["text"] or got["name"] != t["name"]:
            bad.append((t, got))
    print("\n── ผลความถูกต้อง ──")
    print(f"ประโยคที่พูดทั้งหมด {len(truth)}  | ไม่มี event final เลย {missing}  | ข้อความไม่ตรง {len(bad)}")
    for t, got in bad[:3]:
        print(f"  คาดหวัง: {t['text'][:120]}...\n  ได้จริง: {got['text'][:120]}...")

    # ── หน่วยความจำ: เทียบค่าเฉลี่ยหนึ่งในสี่แรกกับหนึ่งในสี่สุดท้าย ──
    ok_mem = True
    if len(samples) >= 8:
        q = len(samples) // 4
        def avg(rows, i):
            return sum(r[i] for r in rows) / len(rows)
        first, last = samples[:q], samples[-q:]
        d_heap = avg(last, 0) - avg(first, 0)
        d_nodes = avg(last, 1) - avg(first, 1)
        d_lis = avg(last, 2) - avg(first, 2)
        print("\n── ผลหน่วยความจำ (ต้นช่วง -> ท้ายช่วง) ──")
        print(f"JS heap  {fmt_mb(avg(first, 0))} -> {fmt_mb(avg(last, 0))} MB (เปลี่ยน {d_heap / 1024 / 1024:+.2f} MB)")
        print(f"DOM nodes {avg(first, 1):.0f} -> {avg(last, 1):.0f} ({d_nodes:+.0f})")
        print(f"listeners {avg(first, 2):.0f} -> {avg(last, 2):.0f} ({d_lis:+.0f})")
        ok_mem = d_heap < 3 * 1024 * 1024 and d_nodes < 200 and d_lis < 50
    else:
        print("\n(สั้นเกินไปที่จะสรุปแนวโน้มหน่วยความจำ — ใช้ --minutes 3 ขึ้นไป)")

    passed = not bad and missing == 0 and ok_mem and len(truth) > 0
    print("\nผล:", "ผ่าน ✓" if passed else "ไม่ผ่าน ✗")
    return 0 if passed else 1


# ── โหมด lifecycle: ทดสอบ watchdog ของเอนจิน ──

LIFECYCLE_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button aria-label="Leave call" id="leave">leave</button>
<div class="a4cQT" style="position:absolute;bottom:0;width:300px;min-height:20px"></div>
</body></html>"""


async def scenario(pw, name, action, expect, engine_kwargs=None, timeout=20):
    events = []
    engine = me.MeetCaptionEngine(on_event=events.append, **(engine_kwargs or {}))
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context()
    page = await context.new_page()
    await page.expose_function("__onCaption", lambda p: events.append(p))
    html = pathlib.Path(tempfile.mkdtemp()) / "lifecycle.html"
    html.write_text(LIFECYCLE_PAGE, encoding="utf-8")
    await page.goto(html.as_uri())
    await engine._install_watcher(page)
    joined = page.locator('[aria-label*="leave" i]').first

    task = asyncio.create_task(engine._monitor(page, context, joined))
    await asyncio.sleep(2)
    early = task.done()
    await action(page, context, engine)

    result = "ยังรันอยู่ (ไม่หยุดเอง)"
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        result = "จบแบบไม่มี exception"
    except me._EngineLost as e:
        result = f"_EngineLost: {e}"
    except me._Stopped:
        result = "_Stopped"
    except asyncio.TimeoutError:
        pass
    extra = ""
    if name.startswith("reload"):
        try:
            extra = f" | watcher={await page.evaluate('!!window.__capWatcherInstalled')}" \
                    f" seq>={await page.evaluate('window.__capSeq')}"
        except Exception as e:  # noqa: BLE001
            extra = f" | evaluate ล้มเหลว {e!r}"
    engine._stop.set()
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    try:
        await browser.close()
    except Exception:  # noqa: BLE001
        pass

    passed = (not early) and expect(result, extra)
    print(f"[{'ผ่าน' if passed else 'ไม่ผ่าน'}] {name}: {result}{extra}")
    return passed


async def run_lifecycle():
    # ย่นเวลาให้ทดสอบไวขึ้น
    me.WATCHDOG_INTERVAL_S = 1
    me.LEFT_ROOM_STRIKES = 2
    me.EVAL_FAIL_STRIKES = 3
    me.REGION_GONE_S = 3
    results = []
    async with async_playwright() as pw:
        async def nothing(page, ctx, eng):
            pass

        async def remove_leave(page, ctx, eng):
            await page.evaluate("document.getElementById('leave').remove()")

        async def close_page(page, ctx, eng):
            await page.close()

        async def reload_page(page, ctx, eng):
            await page.reload()

        results.append(await scenario(
            pw, "ปกติ: ต้องไม่หยุดเอง", nothing,
            lambda r, x: r.startswith("ยังรันอยู่"), timeout=5))
        results.append(await scenario(
            pw, "ประชุมจบ (ปุ่มวางสายหายไป)", remove_leave,
            lambda r, x: "ออกจากห้องประชุมแล้ว" in r, timeout=15))
        results.append(await scenario(
            pw, "หน้าถูกปิด", close_page,
            lambda r, x: "_EngineLost" in r, timeout=15))
        results.append(await scenario(
            pw, "reload: ต้องติดตั้งตัวจับใหม่และนับ id ต่อ", reload_page,
            lambda r, x: r.startswith("ยังรันอยู่") and "watcher=True" in x and "seq>=2000000" in x, timeout=15))
        results.append(await scenario(
            pw, "ครบเวลาสูงสุด", nothing,
            lambda r, x: "ครบเวลาสูงสุด" in r, engine_kwargs={"max_duration_s": 3}, timeout=15))
    ok = all(results)
    print("\nผล:", "ผ่านทุกกรณี ✓" if ok else "มีกรณีที่ไม่ผ่าน ✗")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["soak", "lifecycle"])
    ap.add_argument("--minutes", type=float, default=5)
    args = ap.parse_args()
    if args.mode == "soak":
        sys.exit(asyncio.run(run_soak(args.minutes)))
    sys.exit(asyncio.run(run_lifecycle()))


if __name__ == "__main__":
    main()
