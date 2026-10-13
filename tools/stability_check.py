"""ชุดเครื่องมือทดสอบเสถียรภาพบอทบน Meet จริง (ประชุมยาว ~1 ชั่วโมง)

  1) สร้างบทพูด 90 ประโยค (ประโยคละ 1 บรรทัด พูดทุก ~40 วินาที = 60 นาที):
       .\\venv\\Scripts\\python.exe tools\\stability_check.py make
     ได้ไฟล์ tools\\stability_script.txt  (ผู้พูด 2 คนสลับกันอ่านตามบรรทัด)

  2) ระหว่างประชุม เปิดอีกหน้าต่าง PowerShell วัดหน่วยความจำ (ดู tools\\proc_monitor.ps1)

  3) หลังประชุมจบ เทียบบทพูดกับ transcript ที่บอทบันทึกลงฐานข้อมูล:
       .\\venv\\Scripts\\python.exe tools\\stability_check.py check <meeting_id>
     รายงาน: ประโยคที่บอทจับได้กี่ % (เทียบความคล้ายแบบไม่สนช่องว่าง เผื่อ CC ถอดคำผิดเล็กน้อย), ประโยคที่หายไป,
     ช่วงเวลาที่บันทึกได้ และจำนวนช่วงที่ซ้ำกันเป๊ะ

หมายเหตุ: ผลนี้บอกว่า "บอทจับข้อความได้ครบแค่ไหนตลอดชั่วโมง" ไม่ได้วัดความถูกต้องของ CC ของ Google เอง
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LINES = 90                 # 90 ประโยค x 40 วินาที = 60 นาที
SECONDS_PER_LINE = 40
MATCH_THRESHOLD = 0.6      # ประโยคถือว่า "จับได้" เมื่อความคล้ายกับ transcript ไม่ต่ำกว่านี้ (เผื่อคำที่ CC ถอดผิด)
DEFAULT_SCRIPT = ROOT / "tools" / "stability_script.txt"

TOPICS = [
    "ระบบทะเบียนนักศึกษา", "เครื่องพิมพ์ใหม่ของห้องธุรการ", "การจัดซื้อโน้ตบุ๊ก", "การปรับปรุงห้องประชุมชั้นสอง",
    "รายงานผลการดำเนินงานประจำไตรมาส", "โครงการอบรมเชิงปฏิบัติการ", "ระบบรายงานหน้าเว็บ", "ส่วนค้นหาข้อมูลเอกสาร",
    "การสำรองข้อมูลประจำสัปดาห์", "การตรวจเยี่ยมจากส่วนกลาง", "การจัดทำคู่มือผู้ใช้งาน", "แบบประเมินความพึงพอใจ",
    "การย้ายเซิร์ฟเวอร์ไปห้องใหม่", "งบประมาณค่าเดินทางไปดูงาน", "การทดสอบระบบก่อนเปิดใช้งานจริง",
]
NAMES = ["สมศรี", "ธนาวีร์", "กฤตินล", "ลอง", "วิภา", "ประเสริฐ", "นภา", "อนันต์"]
ISSUES = [
    "ข้อมูลบางส่วนยังไม่ครบ", "เวลาที่นัดไว้ชนกับงานอื่น", "งบประมาณยังไม่ได้รับการอนุมัติ", "อุปกรณ์ยังส่งมอบไม่ครบ",
    "ผู้รับผิดชอบยังไม่ยืนยันกำหนดส่ง", "ระบบยังตอบสนองช้ากว่าที่คาดไว้",
]
DAYS = ["วันจันทร์หน้า", "วันพุธหน้า", "วันศุกร์หน้า", "ต้นเดือนหน้า", "สิ้นเดือนนี้"]


def make_lines(n: int = LINES) -> list[str]:
    """บทพูด n ประโยค ไม่ซ้ำกันสักประโยค (มีเลขข้อกำกับ) เนื้อหาเป็นประโยคประชุมทั่วไป"""
    out = []
    for i in range(n):
        topic, name, issue, day = TOPICS[i % len(TOPICS)], NAMES[i % len(NAMES)], ISSUES[(i // 2) % len(ISSUES)], DAYS[(i // 3) % len(DAYS)]
        pct, amt = 10 + (i * 7) % 90, 5000 + (i * 1300) % 90000
        pattern = (i // len(TOPICS)) % 6          # (หัวข้อ, รูปแบบประโยค) ไม่ซ้ำกันทั้ง 90 ประโยค
        if pattern == 0:
            s = f"ข้อที่ {i + 1} เรื่อง{topic} ตอนนี้ความคืบหน้าอยู่ที่ประมาณ {pct} เปอร์เซ็นต์ และคาดว่าจะเสร็จภายใน{day}ครับ"
        elif pattern == 1:
            s = f"ข้อที่ {i + 1} ผมเสนอให้{topic}ใช้งบประมาณ {amt:,} บาท เพราะต้นทุนเดิมสูงเกินไปครับ"
        elif pattern == 2:
            s = f"ข้อที่ {i + 1} ดิฉันเห็นว่าควรให้คุณ{name}รับผิดชอบเรื่อง{topic} และรายงานผลภายใน{day}ค่ะ"
        elif pattern == 3:
            s = f"ข้อที่ {i + 1} มีปัญหาเรื่อง{topic}ที่ต้องแก้ไข คือ{issue} ขอให้ทุกคนช่วยกันตรวจสอบครับ"
        elif pattern == 4:
            s = f"ข้อที่ {i + 1} ที่ประชุมเห็นชอบให้{topic}ดำเนินการต่อ โดยนัดประชุมติดตามอีกครั้ง{day}ครับ"
        else:
            s = f"ข้อที่ {i + 1} ขอสรุปเรื่อง{topic}ว่ายังไม่มีข้อตกลงที่ชัดเจน ขอยกไปพิจารณาในการประชุมครั้งหน้าค่ะ"
        out.append(s)
    return out


def script_text(lines: list[str]) -> str:
    head = ["บทพูดทดสอบเสถียรภาพบอท (ประโยคละ 1 บรรทัด)",
            f"วิธีใช้: ผู้พูด 2 คนสลับกันอ่านทีละบรรทัดตามลำดับ ทุก ~{SECONDS_PER_LINE} วินาที (บรรทัดที่ 1 เริ่มนาทีที่ 0)",
            "อ่านชัดๆ ไม่เร็ว หยุดเว้นช่วงระหว่างประโยคให้ Meet ถอดเสียงทัน ห้ามข้ามบรรทัด (ถ้าพลาดให้จดเลขบรรทัดไว้)",
            "คอลัมน์: เลขบรรทัด | นาที:วินาทีที่ควรพูด | ผู้พูด | ประโยค", ""]
    body = []
    for i, line in enumerate(lines):
        t = i * SECONDS_PER_LINE
        body.append(f"{i + 1:>3} | {t // 60:02d}:{t % 60:02d} | {'A' if i % 2 == 0 else 'B'} | {line}")
    return "\n".join(head + body) + "\n"


def read_script(path: pathlib.Path) -> list[str]:
    """อ่านเฉพาะบรรทัดที่เป็นแถวของบทพูด (รูปแบบ 'เลข | เวลา | ผู้พูด | ประโยค')"""
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in raw.split("|", 3)]
        if len(parts) == 4 and parts[0].isdigit():
            lines.append(parts[3])
    return lines


def match_lines(script: list[str], transcript_texts: list[str]) -> list[float]:
    """คะแนนว่าประโยคในบทปรากฏใน transcript แค่ไหน (0–1)

    ประโยคในบทมีโครงคล้ายกัน จึงวัดเฉพาะ "ส่วนที่เป็นของประโยคนั้นประโยคเดียว" (ชิ้นข้อความ 4 ตัวอักษรที่ไม่ซ้ำกับประโยคอื่นในบท)
    ไม่งั้นประโยคที่หายไปจะยังได้คะแนนสูงจากวลีที่ซ้ำกับประโยคอื่น"""
    from collections import Counter

    from reports import summarizer
    per_line = [summarizer._shingles(line) for line in script]
    count = Counter(sh for shs in per_line for sh in shs)
    have = summarizer.transcript_shingles(transcript_texts)
    scores = []
    for shs in per_line:
        mine = {sh for sh in shs if count[sh] == 1} or shs
        scores.append(len(mine & have) / len(mine) if mine else 0.0)
    return scores


def duplicate_segments(transcript_texts: list[str]) -> int:
    """จำนวนช่วงที่ข้อความซ้ำกับช่วงก่อนหน้าเป๊ะ (ไม่สนช่องว่าง) — สัญญาณว่าบอทบันทึกซ้ำ"""
    from reports import summarizer
    seen, dups = set(), 0
    for t in transcript_texts:
        key = summarizer._squash(t)
        if key in seen:
            dups += 1
        seen.add(key)
    return dups


def segments_per_bucket(rows: list[dict], minutes: int = 10) -> list[int]:
    """จำนวนช่วงที่บันทึกได้ในแต่ละช่วงเวลา (ทุก `minutes` นาทีนับจากช่วงแรก) — ใช้ดูว่ามีช่วงที่เงียบหายผิดปกติหรือไม่
    (ใช้กับการคุยอิสระที่ไม่มีบทพูดได้ แต่แยก "ไม่มีใครพูด" ออกจาก "บอทไม่จับ" ไม่ได้)"""
    if not rows:
        return []
    start = rows[0]["spoken_at"]
    buckets = [0] * (int((rows[-1]["spoken_at"] - start).total_seconds() // (minutes * 60)) + 1)
    for r in rows:
        buckets[int((r["spoken_at"] - start).total_seconds() // (minutes * 60))] += 1
    return buckets


def report(script: list[str], rows: list[dict], only: list[int] | None = None) -> tuple[str, bool]:
    """only = เช็กเฉพาะบรรทัดที่ระบุ (เลขบรรทัดเริ่มที่ 1) เช่น ประโยคจุดตรวจที่พูดแทรกระหว่างคุยอิสระ"""
    if only:
        script = [script[i - 1] for i in only if 1 <= i <= len(script)]
    texts = [r["text"] for r in rows]
    scores = match_lines(script, texts)
    caught = [s >= MATCH_THRESHOLD for s in scores]
    n, ok = len(script), sum(caught)
    out = [f"บทพูด {n} ประโยค  |  ช่วงที่บอทบันทึกใน DB {len(rows)} ช่วง",
           f"จับได้ {ok}/{n} = {100 * ok / n:.1f}%  (นับว่าจับได้เมื่อคล้ายบทพูด ≥ {int(MATCH_THRESHOLD * 100)}%)"]
    if rows:
        first, last = rows[0]["spoken_at"], rows[-1]["spoken_at"]
        out.append(f"ช่วงเวลาที่บันทึกได้: {first:%H:%M:%S} ถึง {last:%H:%M:%S}  (~{(last - first).total_seconds() / 60:.0f} นาที)")
    out.append(f"ช่วงที่ซ้ำกันเป๊ะ (สงสัยบันทึกซ้ำ): {duplicate_segments(texts)}")
    out.append("จำนวนช่วงที่บันทึกได้ทุก 10 นาที: " + ", ".join(str(b) for b in segments_per_bucket(rows)))
    missing = [i for i, c in enumerate(caught) if not c]
    if missing:
        def label(i: int) -> str:
            if only:
                return str(only[i])
            return f"{i + 1} ({i * SECONDS_PER_LINE // 60:02d}:{i * SECONDS_PER_LINE % 60:02d})"
        out.append("ประโยคที่หายไป (เลขบรรทัด" + ("" if only else " · นาทีที่ควรพูด") + "): " +
                   ", ".join(label(i) for i in missing[:40]) + (" …" if len(missing) > 40 else ""))
        if not only:    # ช่วงที่หายต่อเนื่องยาว = บอทหลุด/ตัวจับหยุดทำงาน ไม่ใช่ CC ถอดผิดทีละประโยค
            longest, run = 0, 0
            for c in caught:
                run = 0 if c else run + 1
                longest = max(longest, run)
            out.append(f"ช่วงที่หายติดต่อกันยาวสุด: {longest} ประโยค (~{longest * SECONDS_PER_LINE // 60} นาที)")
    return "\n".join(out), ok / n >= 0.95 if n else False


def main():
    ap = argparse.ArgumentParser(description="เครื่องมือทดสอบเสถียรภาพบอท")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="สร้างไฟล์บทพูด")
    mk.add_argument("--out", default=str(DEFAULT_SCRIPT))
    ck = sub.add_parser("check", help="เทียบบทพูดกับ transcript ของการประชุม")
    ck.add_argument("meeting_id", type=int)
    ck.add_argument("--script", default=str(DEFAULT_SCRIPT))
    ck.add_argument("--only", default="", help="เช็กเฉพาะบรรทัดที่ระบุ คั่นด้วยจุลภาค เช่น 1,16,31,46,61,76 (ใช้กับการคุยอิสระที่แทรกประโยคจุดตรวจ)")
    args = ap.parse_args()

    if args.cmd == "make":
        out = pathlib.Path(args.out)
        out.write_text(script_text(make_lines()), encoding="utf-8")
        print(f"สร้างบทพูด {LINES} ประโยคที่ {out}")
        return 0

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")        # ต้องโหลดก่อน import db เพราะ db.py อ่านค่าเชื่อมต่อตอน import
    import db
    script = read_script(pathlib.Path(args.script))
    rows = db.get_transcript(args.meeting_id)
    if not script or not rows:
        print("ไม่พบบทพูดหรือ transcript ของการประชุมนี้")
        return 2
    only = [int(x) for x in args.only.split(",") if x.strip().isdigit()] or None
    text, passed = report(script, rows, only)
    print(text)
    print("\nเกณฑ์ที่เสนอ: จับได้ ≥ 95% และไม่มีช่วงที่หายติดต่อกันยาว  →  " + ("ผ่าน" if passed else "ยังไม่ผ่าน (ดูประโยคที่หายไป)"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
