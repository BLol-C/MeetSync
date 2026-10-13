"""สร้างใบตรวจคุณภาพรายงานที่ AI ร่าง (ตรวจด้วยมือ ไม่ใช้ AI ตัดสิน AI)

  .\\venv\\Scripts\\python.exe tools\\review_sheet.py <meeting_id>

ได้ไฟล์ ai_review\\meeting_<id>.md (โฟลเดอร์นี้ไม่ถูก commit เพราะมีเนื้อหาประชุมจริง) ประกอบด้วย
  1) สิ่งที่คนแก้หลัง AI ร่าง — คำนวณอัตโนมัติโดยเทียบฉบับ AI เดิม (ai_snapshot) กับฉบับล่าสุด
     (ยิ่งคนต้องแก้น้อย แปลว่า AI ร่างมาใกล้ความจริง เป็นสัญญาณฟรีที่ไม่ต้องรอเฉลย)
  2) ใบตรวจรายวาระ/รายงาน พร้อมข้อความอ้างอิง ให้ผู้ที่อยู่ในประชุมติ [x] ถูก/ผิด
  3) ช่องเขียนสิ่งที่ AI ตกหล่น และตารางรวมคะแนน
  4) transcript ทั้งหมดไว้เทียบ

หมายเหตุ: ใบนี้ช่วยให้ตรวจเป็นระบบ ไม่ได้ตรวจแทนคน ผลที่ได้คือตัวเลขจากการตรวจของคนในประชุมจริง
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SECTION_LABEL = {
    "inform": "วาระ 1 เรื่องแจ้งให้ที่ประชุมทราบ",
    "consider_old": "วาระ 4.1 เรื่องพิจารณาเดิม",
    "consider_new": "วาระ 4.2 เรื่องพิจารณาใหม่",
}
OUT_DIR = ROOT / "ai_review"


def _norm(s) -> str:
    return " ".join((s or "").split())


def compare(snapshot: dict | None, content: dict) -> dict:
    """เทียบฉบับ AI เดิมกับฉบับที่คนแก้แล้ว — คืนจำนวนที่คนเพิ่ม/ลบ/แก้ (จับคู่วาระด้วยชื่อวาระ งานด้วยคำอธิบายงาน)
    snapshot เป็น None = ไม่มีฉบับเดิมให้เทียบ"""
    if not snapshot:
        return {"available": False}
    out = {"available": True, "summary_changed": _norm(snapshot.get("summary")) != _norm(content.get("summary"))}

    def diff(before: list[dict], after: list[dict], key: str, fields: tuple[str, ...]) -> dict:
        b = {_norm(x.get(key)): x for x in before}
        a = {_norm(x.get(key)): x for x in after}
        changed = sorted(k for k in b.keys() & a.keys() if any(_norm(b[k].get(f)) != _norm(a[k].get(f)) for f in fields))
        return {"ai_total": len(before), "final_total": len(after),
                "unchanged": len(b.keys() & a.keys()) - len(changed),
                "changed": changed, "added_by_human": sorted(a.keys() - b.keys()), "removed_by_human": sorted(b.keys() - a.keys())}

    out["agenda"] = diff(snapshot.get("agenda") or [], content.get("agenda") or [], "title", ("discussion", "resolution", "section"))
    out["actions"] = diff(snapshot.get("action_items") or [], content.get("action_items") or [], "description",
                          ("assignee", "due_date", "due_time", "due_time_end"))
    return out


def _diff_lines(label: str, d: dict) -> list[str]:
    lines = [f"- {label}: AI ร่าง {d['ai_total']} รายการ → ฉบับสุดท้าย {d['final_total']} รายการ"
             f" (ไม่ถูกแก้ {d['unchanged']}, แก้เนื้อหา {len(d['changed'])}, คนเพิ่ม {len(d['added_by_human'])}, คนลบ/เปลี่ยนชื่อ {len(d['removed_by_human'])})"]
    for title, items in (("แก้เนื้อหา", d["changed"]), ("คนเพิ่ม", d["added_by_human"]), ("คนลบ/เปลี่ยนชื่อ", d["removed_by_human"])):
        lines += [f"    - {title}: {x}" for x in items]
    return lines


def build_sheet(meeting: dict, report: dict, transcript: list[dict]) -> str:
    content = report["content"]
    cmp_ = compare(report.get("ai_snapshot"), content)
    L = [f"# ใบตรวจคุณภาพรายงาน AI — {meeting['title'] or '(ไม่ได้ตั้งชื่อ)'} (id {meeting['meeting_id']})", "",
         f"- โมเดล: {report.get('model_used') or '-'} · prompt: {report.get('prompt_version') or '-'} · ร่างเมื่อ {report.get('generated_at')}",
         f"- ผู้ตรวจ: ____________  วันที่ตรวจ: ____________  (ต้องเป็นผู้ที่อยู่ในประชุมนั้นจริง)", "",
         "## 1) คนแก้อะไรหลังจาก AI ร่าง (คำนวณอัตโนมัติ)", ""]
    if not cmp_["available"]:
        L.append("- ไม่มีฉบับ AI เดิมให้เทียบ")
    else:
        L.append(f"- สรุปภาพรวม: {'คนแก้' if cmp_['summary_changed'] else 'ไม่ถูกแก้'}")
        L += _diff_lines("วาระ", cmp_["agenda"]) + _diff_lines("งาน", cmp_["actions"])
        L.append("- ถ้ารายงานยังไม่เคยถูกแก้เลย ตัวเลขนี้ไม่บอกอะไร (ยังไม่มีคนตรวจ) ให้ใช้ใบตรวจข้างล่างแทน")

    L += ["", "## 2) ใบตรวจรายวาระ", "",
          "ติด [x] ในช่องที่ใช่ แล้วเขียนหมายเหตุสั้นๆ ถ้าผิด", ""]
    for i, a in enumerate(content.get("agenda") or [], 1):
        L += [f"### วาระที่ {i}: {a['title']}  ({SECTION_LABEL.get(a.get('section'), a.get('section'))})", "",
              f"**AI สรุปว่า:** {a.get('discussion') or '-'}", "",
              f"**มติที่ AI เขียน:** {a.get('resolution') or '(ไม่มีมติ — เว้นว่าง)'}", ""]
        for q in a.get("evidence") or []:
            L.append(f"> อ้างอิง: {q}")
        L += ["",
              "- เนื้อหาที่ AI สรุปตรงกับที่พูดจริง:  [ ] ถูก  [ ] ผิดบางส่วน  [ ] ผิด",
              "- มติ:  [ ] มติตรงกับที่ตกลงจริง  [ ] ที่ประชุมไม่ได้มีมติแต่ AI เขียนมติ (**แต่งมติ**)  [ ] มีมติจริงแต่ AI เว้นว่าง  [ ] ไม่มีมติและเว้นว่างถูกต้อง",
              "- หมวดของวาระ (แจ้งทราบ/พิจารณา):  [ ] ถูก  [ ] ผิด", "- หมายเหตุ: ", ""]

    L += ["## 3) ใบตรวจรายการงาน", ""]
    for i, t in enumerate(content.get("action_items") or [], 1):
        due = t.get("due_date") or "(ไม่มีกำหนด)"
        L += [f"### งานที่ {i}: {t['description']}", "",
              f"ผู้รับผิดชอบ: {t.get('assignee') or '-'} · กำหนด: {due}"
              + (f" {t['due_time']}" if t.get("due_time") else ""), ""]
        for q in t.get("evidence") or []:
            L.append(f"> อ้างอิง: {q}")
        L += ["",
              "- งานนี้มีในการประชุมจริง:  [ ] มี  [ ] ไม่มี (AI แต่งขึ้น)",
              "- ผู้รับผิดชอบ:  [ ] ถูก  [ ] ผิด  [ ] ไม่มีใครรับ แต่ AI ระบุชื่อ",
              "- วันที่/เวลา:  [ ] ถูก  [ ] ผิด  [ ] ไม่มีใครพูดถึงวัน แต่ AI ใส่วัน (**เดาวัน**)  [ ] ไม่มีวันและเว้นว่างถูกต้อง",
              "- หมายเหตุ: ", ""]

    L += ["## 4) สิ่งที่ AI ตกหล่น (เขียนเอง)", "",
          "| # | วาระ/งานที่ประชุมพูดจริงแต่ไม่อยู่ในรายงาน | วาระหรืองาน |", "|---|---|---|", "| 1 | | |", "| 2 | | |", "| 3 | | |", "",
          "## 5) ตารางรวมคะแนน (นับจากใบตรวจข้างบน)", "",
          "| หัวข้อ | จำนวน | จาก | หมายเหตุ |", "|---|---|---|---|",
          f"| วาระที่เนื้อหาถูก | | {len(content.get('agenda') or [])} | |",
          "| วาระที่ **แต่งมติ** (ร้ายแรงสุด) | | | |",
          "| วาระที่ตกหล่น (ข้อ 4) | | | |",
          f"| งานที่มีจริง | | {len(content.get('action_items') or [])} | |",
          "| งานที่ผู้รับผิดชอบถูก | | | |",
          "| งานที่วันที่ถูก | | | |",
          "| งานที่ **เดาวัน** | | | |",
          "| ข้อความอ้างอิงที่ไม่มีใน transcript | | | (ดูคำเตือนในแท็บ ④) |", "",
          "## 6) transcript ที่ใช้ตรวจ", ""]
    for r in transcript:
        when = r["spoken_at"].strftime("%H:%M:%S") if r.get("spoken_at") else ""
        L.append(f"- [{when}] {r['display_name']}: {r['text']}")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("meeting_id", type=int)
    args = ap.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")        # ต้องโหลดก่อน import db เพราะ db.py อ่านค่าเชื่อมต่อตอน import
    import db
    meeting = db.get_meeting(args.meeting_id)
    report = db.get_report(args.meeting_id)
    if not meeting or not report:
        sys.exit("ไม่พบการประชุมนี้ หรือยังไม่มีรายงานที่ AI ร่าง (ให้ AI ร่างรายงานที่แท็บ ④ ก่อน)")
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"meeting_{args.meeting_id}.md"
    path.write_text(build_sheet(meeting, report, db.get_transcript(args.meeting_id)), encoding="utf-8")
    print(f"สร้างใบตรวจแล้ว: {path}")


if __name__ == "__main__":
    main()
