"""
ประเมินคุณภาพรายงานที่ AI ร่าง กับชุดการประชุมที่มีเฉลย แล้วสรุปเป็นตาราง (เก็บไว้ที่ evaluation/results/)

รัน (ใช้ Gemini จริง ต้องมี GEMINI_API_KEY ใน .env):
  venv\\Scripts\\python.exe evaluation\\run_eval.py
  venv\\Scripts\\python.exe evaluation\\run_eval.py --repeat 3          # รัน 3 รอบแล้วเฉลี่ย (AI ตอบไม่เหมือนกันทุกรอบ)
  venv\\Scripts\\python.exe evaluation\\run_eval.py --case example_budget_meeting

รูปแบบเคส: evaluation/cases/<ชื่อเคส>/ มี transcript.txt ("ชื่อ: ข้อความ" บรรทัดละช่วง) กับ case.json
(ดู evaluation/README.md)
"""

import argparse
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import summarizer  # noqa: E402
from evaluation import metrics  # noqa: E402

CASES_DIR = ROOT / "evaluation" / "cases"
RESULTS_DIR = ROOT / "evaluation" / "results"

LABELS = {
    "agenda_recall": "ครบถ้วนของวาระ (recall)",
    "resolution_accuracy": "ความถูกต้องของมติ",
    "false_resolution_rate": "มติที่แต่งขึ้น (↓)",
    "action_recall": "ครบถ้วนของงาน (recall)",
    "action_precision": "งานที่ถูกต้อง (precision)",
    "assignee_accuracy": "ผู้รับผิดชอบถูกคน",
    "date_accuracy": "วันกำหนดถูกต้อง",
    "ungrounded_rate": "อ้างอิงไม่ได้ (↓)",
    "unflagged_wrong": "ผิดแต่ตรวจไม่เจอ (↓)",
}


def load_case(path: pathlib.Path) -> dict:
    cfg = json.loads((path / "case.json").read_text(encoding="utf-8"))
    when = datetime.datetime.fromisoformat(cfg["date"] + "T09:00:00")
    rows = []
    for i, line in enumerate((path / "transcript.txt").read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, text = line.split(":", 1)
        rows.append({"display_name": name.strip(), "text": text.strip(), "spoken_at": when + datetime.timedelta(seconds=30 * i)})
    parts = [{"participant_id": k + 1, "attendance": "present", "email": None, **p} for k, p in enumerate(cfg["participants"])]
    return {"name": path.name, "title": cfg.get("title"), "when": when, "rows": rows, "participants": parts,
            "expected": cfg["expected"], "synthetic": cfg.get("synthetic", False)}


def fmt(v, key):
    if v is None:
        return "-"
    return str(int(v)) if key == "unflagged_wrong" else f"{v * 100:.0f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="ชื่อเคสเดียว (ไม่ระบุ = ทุกเคสใน evaluation/cases)")
    ap.add_argument("--repeat", type=int, default=1, help="จำนวนรอบต่อเคส (เฉลี่ยผล)")
    args = ap.parse_args()

    cases = [load_case(p) for p in sorted(CASES_DIR.iterdir()) if p.is_dir() and (not args.case or p.name == args.case)]
    if not cases:
        sys.exit(f"ไม่พบเคสใน {CASES_DIR}")

    per_case, all_runs, failures = {}, [], 0
    for case in cases:
        runs = []
        for r in range(args.repeat):
            print(f"[{case['name']}] รอบ {r + 1}/{args.repeat} …", flush=True)
            try:
                content = summarizer.generate_minutes_content(
                    case["rows"], case["participants"], case["title"], case["when"]
                )
            except Exception as e:  # noqa: BLE001 — นับเป็นรอบที่ล้มเหลว ไม่ให้ทั้งชุดหยุด
                failures += 1
                print(f"   ✗ สร้างรายงานไม่สำเร็จ: {e}")
                continue
            runs.append(metrics.evaluate(content, case["expected"], [p["display_name"] for p in case["participants"]]))
        per_case[case["name"]] = metrics.average(runs) if runs else None
        all_runs += runs

    overall = metrics.average(all_runs) if all_runs else None
    if overall is None:
        sys.exit("ทุกรอบสร้างรายงานไม่สำเร็จ จึงไม่มีผลให้สรุป (ไม่บันทึกไฟล์) — ลองใหม่ภายหลัง หากเป็น 503 คือ Gemini โหลดสูงชั่วคราว")
    lines = [
        f"# ผลประเมินคุณภาพรายงานที่ AI ร่าง",
        "",
        f"- วันที่รัน: {datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"- โมเดล: `{summarizer._MODEL}`  · prompt: `{summarizer.PROMPT_NAME}`",
        f"- จำนวนเคส: {len(cases)}  · รอบต่อเคส: {args.repeat}  · รอบที่สร้างรายงานไม่สำเร็จ: {failures}",
    ]
    if any(c["synthetic"] for c in cases):
        lines.append("- ⚠️ มีเคสสังเคราะห์ปนอยู่ (ไม่ใช่การประชุมจริง) — ห้ามใช้ตัวเลขนี้อ้างเป็นผลประเมินจริง")
    lines += ["", "| ตัวชี้วัด | " + " | ".join(per_case) + " | **รวม** | เกณฑ์ผ่าน | ผล |", "|---|" + "---|" * (len(per_case) + 3)]
    verdicts = metrics.verdict(overall) if overall else {}
    for key, label in LABELS.items():
        limit, kind = metrics.THRESHOLDS[key]
        crit = (f"≥ {limit * 100:.0f}%" if kind == "min" else f"≤ {limit * 100:.0f}%") if key != "unflagged_wrong" else "= 0"
        cells = [fmt(per_case[c][key] if per_case[c] else None, key) for c in per_case]
        lines.append(f"| {label} | " + " | ".join(cells) + f" | **{fmt(overall[key] if overall else None, key)}** | {crit} | {verdicts.get(key, '-')} |")
    report = "\n".join(lines) + "\n"
    print("\n" + report)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text(report, encoding="utf-8")
    print(f"บันทึกผลไว้ที่ {out}")
    sys.exit(1 if (failures or any(v == "ไม่ผ่าน" for v in verdicts.values())) else 0)


if __name__ == "__main__":
    main()
