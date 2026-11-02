"""เทียบสองรุ่นของรายงาน (ประวัติการแก้ไข) — คืนโครงที่หน้าเว็บเอาไปแสดงได้เลย ไม่แตะฐานข้อมูล

ข้อความภาษาไทยไม่มีช่องว่างคั่นคำ จึงเทียบทีละตัวอักษร แล้วรวมช่วงที่ตรงกันสั้น ๆ (เช่น "ที่" ที่บังเอิญตรงกัน) เข้ากับช่วงที่ต่างกัน
ไม่งั้นผลเป็นเศษสีสลับถี่จนอ่านไม่ออก
"""

from difflib import SequenceMatcher

KIND_LABEL = {
    "ai_draft": "AI ร่าง",
    "human_edit": "คนแก้ไข",
    "calendar_sync": "ซิงก์จาก Calendar",
    "approve": "อนุมัติ",
    "reopen": "ยกเลิกการอนุมัติ",
    "baseline": "ฉบับเริ่มต้น (ก่อนเริ่มเก็บประวัติ)",
}
MIN_EQUAL = 3          # ช่วงที่ตรงกันสั้นกว่านี้ที่คั่นอยู่ระหว่างส่วนที่ต่าง ถือว่าเป็นส่วนที่ต่างต่อเนื่อง
OTHER_KIND = "อื่น ๆ"       # ชนิดที่ไม่รู้จัก ห้ามโชว์ค่าภาษาอังกฤษดิบให้ผู้ใช้เห็น
SIMILAR = 0.5          # ชื่อวาระ/งานที่คล้ายกันเกินนี้ถือว่าเป็นเรื่องเดียวกันที่ถูกแก้ ไม่ใช่ลบอันหนึ่งแล้วเพิ่มอีกอัน


def text_diff(old: str | None, new: str | None) -> list[tuple[str, str]]:
    """[(op, ข้อความ)] โดย op = equal / del / ins — del คือตัวที่มีเฉพาะรุ่นเก่า ins คือตัวที่มีเฉพาะรุ่นใหม่"""
    old, new = old or "", new or ""
    raw: list[tuple[str, str, str]] = []     # (op, ข้อความเก่า, ข้อความใหม่)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        raw.append(("equal" if tag == "equal" else "change", old[i1:i2], new[j1:j2]))
    # ช่วงที่ตรงกันสั้น ๆ ระหว่างสองช่วงที่ต่าง -> รวมเป็นช่วงต่างเดียวกัน
    merged: list[list[str]] = []
    for k, (op, a, b) in enumerate(raw):
        short_between = (op == "equal" and len(a) < MIN_EQUAL and 0 < k < len(raw) - 1
                         and raw[k - 1][0] == "change" and raw[k + 1][0] == "change")
        op = "change" if short_between else op
        if merged and merged[-1][0] == op:
            merged[-1][1] += a
            merged[-1][2] += b
        else:
            merged.append([op, a, b])
    out: list[tuple[str, str]] = []
    for op, a, b in merged:
        if op == "equal":
            out.append(("equal", a))
        else:
            if a:
                out.append(("del", a))
            if b:
                out.append(("ins", b))
    return out


def _changed(ops: list[tuple[str, str]]) -> bool:
    return any(op != "equal" for op, _ in ops)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a or "", b or "", autojunk=False).ratio()


def _pair(old: list[dict], new: list[dict], keys: tuple[str, ...], group: str | None = None) -> list[tuple[int | None, int | None]]:
    """จับคู่รายการรุ่นเก่ากับรุ่นใหม่ว่าเป็นเรื่องเดียวกันที่ถูกแก้ หรือเป็นของที่ถูกเพิ่ม/ลบ
    1) ตัวแรกของ keys (ชื่อ/คำอธิบาย) ตรงกันเป๊ะ  2) คล้ายกันที่สุดจาก keys ตัวใดตัวหนึ่ง (เกิน SIMILAR)
    3) ที่เหลือฝั่งละหนึ่งรายการในหมวดเดียวกัน ถือเป็นเรื่องเดียวกัน (ชื่อถูกแก้จนไม่เหลือส่วนที่เหมือน)
    คืน [(index เก่า|None, index ใหม่|None)] เรียงตามลำดับรุ่นใหม่ แล้วตามด้วยที่ถูกลบ"""
    pairs: dict[int, int] = {}              # index ใหม่ -> index เก่า
    used: set[int] = set()
    for j, n in enumerate(new):
        for i, o in enumerate(old):
            if i not in used and o[keys[0]] == n[keys[0]]:
                pairs[j] = i
                used.add(i)
                break
    for j, n in enumerate(new):
        if j in pairs:
            continue
        best, best_ratio = None, SIMILAR
        for i, o in enumerate(old):
            if i in used:
                continue
            ratio = max((_ratio(o[k], n[k]) for k in keys if len(o[k]) >= 8 and len(n[k]) >= 8), default=0)   # ข้อความสั้น/ว่างเปล่าไม่ใช้เป็นหลักฐานว่าเป็นเรื่องเดียวกัน
            if ratio >= best_ratio:
                best, best_ratio = i, ratio
        if best is not None:
            pairs[j] = best
            used.add(best)
    if group:
        left_new = [j for j in range(len(new)) if j not in pairs]
        left_old = [i for i in range(len(old)) if i not in used]
        for g in {new[j][group] for j in left_new}:
            ns = [j for j in left_new if new[j][group] == g]
            os_ = [i for i in left_old if old[i][group] == g]
            if len(ns) == 1 and len(os_) == 1:
                pairs[ns[0]] = os_[0]
                used.add(os_[0])
    out = [(pairs.get(j), j) for j in range(len(new))]
    out += [(i, None) for i in range(len(old)) if i not in used]
    return out


def _when(it: dict | None) -> str:
    if not it:
        return ""
    parts = [(it.get("due_date") or "") + (f" ถึง {it['due_date_end']}" if it.get("due_date_end") else "")]
    if it.get("due_time"):
        parts.append(it["due_time"] + (f"–{it['due_time_end']}" if it.get("due_time_end") else ""))
    return " ".join(p for p in parts if p)


def diff_reports(old: dict, new: dict) -> dict:
    """เทียบรายงานสองรุ่น (รูปแบบเนื้อหาจาก db.get_report_version)
    คืน {"changed": bool, "summary": ops, "other_matters": ops,
         "agenda": [{status: added|removed|changed|same, section, title, discussion, resolution (ops), section_changed}],
         "action_items": [{status, description (ops), assignee (ops), when (ops), evidence_changed}]}"""
    agenda = []
    o_ag, n_ag = old.get("agenda") or [], new.get("agenda") or []
    for i, j in _pair(o_ag, n_ag, ("title", "discussion"), group="section"):
        o, n = (o_ag[i] if i is not None else None), (n_ag[j] if j is not None else None)
        base = n or o
        entry = {
            "section": base["section"], "section_changed": bool(o and n and o["section"] != n["section"]),
            "old_section": o["section"] if o else None,
            "title": text_diff(o["title"] if o else "", n["title"] if n else ""),
            "discussion": text_diff(o["discussion"] if o else "", n["discussion"] if n else ""),
            "resolution": text_diff(o["resolution"] if o else "", n["resolution"] if n else ""),
        }
        if o is None:
            entry["status"] = "added"
        elif n is None:
            entry["status"] = "removed"
        else:
            entry["status"] = "changed" if (entry["section_changed"] or any(
                _changed(entry[k]) for k in ("title", "discussion", "resolution"))) else "same"
        agenda.append(entry)

    actions = []
    o_ac, n_ac = old.get("action_items") or [], new.get("action_items") or []
    for i, j in _pair(o_ac, n_ac, ("description",)):
        o, n = (o_ac[i] if i is not None else None), (n_ac[j] if j is not None else None)
        entry = {
            "description": text_diff(o["description"] if o else "", n["description"] if n else ""),
            "assignee": text_diff((o or {}).get("assignee") or "", (n or {}).get("assignee") or ""),
            "when": text_diff(_when(o), _when(n)),
        }
        if o is None:
            entry["status"] = "added"
        elif n is None:
            entry["status"] = "removed"
        else:
            entry["status"] = "changed" if any(_changed(entry[k]) for k in ("description", "assignee", "when")) else "same"
        actions.append(entry)

    summary = text_diff(old.get("summary"), new.get("summary"))
    other = text_diff(old.get("other_matters"), new.get("other_matters"))
    return {
        "summary": summary, "other_matters": other, "agenda": agenda, "action_items": actions,
        "changed": _changed(summary) or _changed(other)
                   or any(a["status"] != "same" for a in agenda) or any(a["status"] != "same" for a in actions),
    }
