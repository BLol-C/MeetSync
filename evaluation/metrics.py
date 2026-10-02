"""
เกณฑ์วัดคุณภาพรายงานที่ AI ร่าง เทียบกับ "เฉลย" ที่คนเขียน — คำนวณแบบตายตัว ไม่ใช้ AI ตัดสิน AI

เฉลย (expected) ระบุด้วยคำสำคัญ ไม่ใช่ข้อความเต็ม เพราะ AI เรียบเรียงถ้อยคำต่างกันได้แต่ใจความต้องตรง:
  {"agenda":  [{"topic": ["งบ", "ประมาณ"], "resolution": ["อนุมัติ", "20,000"] หรือ null (ไม่มีมติ)}],
   "action_items": [{"keywords": ["รายงาน", "ความก้าวหน้า"], "assignee": "Alice", "due_date": "2026-10-09" หรือ null}]}

รายการหนึ่ง "ตรงกับเฉลย" เมื่อมีคำสำคัญอย่างน้อย MATCH_THRESHOLD (60%) ปรากฏในข้อความของรายการนั้น

ตัวชี้วัด (ยิ่งสูงยิ่งดี ยกเว้นที่ระบุว่า "ยิ่งต่ำยิ่งดี"):
  agenda_recall        วาระในเฉลยที่ AI สกัดได้ครบ
  resolution_accuracy  มติถูกต้อง (วาระที่มีมติต้องได้มติตรง / วาระที่ไม่มีมติต้องไม่ถูกแต่งมติขึ้นมา)
  false_resolution_rate (ยิ่งต่ำยิ่งดี) วาระที่ไม่มีมติจริงแต่ AI ใส่มติให้ — การแต่งเรื่องที่อันตรายที่สุดในรายงานทางการ
  action_recall        งานในเฉลยที่ AI สกัดได้
  action_precision     งานที่ AI สกัดมาแล้วมีอยู่จริงในเฉลย (ไม่แต่งงานเพิ่ม)
  assignee_accuracy    ผู้รับผิดชอบถูกคน (เฉพาะงานที่จับคู่กับเฉลยได้)
  date_accuracy        วันกำหนดถูกต้อง (เฉพาะงานที่เฉลยมีวันกำหนด)
  ungrounded_rate (ยิ่งต่ำยิ่งดี) มติ/งานที่ระบบตรวจพบว่าอ้างอิง transcript ไม่ได้
  unflagged_wrong      (ยิ่งต่ำยิ่งดี) จำนวนงานที่ผิดจริงแต่ระบบตรวจไม่เจอ = ช่องโหว่ของชั้นตรวจคุณภาพ
"""

import re

MATCH_THRESHOLD = 0.6

# เกณฑ์ผ่านขั้นต่ำที่เสนอ (ปรับได้ตามที่ตกลงกับอาจารย์) — (ค่าที่ต้องการ, "min" = ต้องไม่ต่ำกว่า / "max" = ต้องไม่สูงกว่า)
THRESHOLDS = {
    "agenda_recall": (0.80, "min"),
    "resolution_accuracy": (0.80, "min"),
    "false_resolution_rate": (0.10, "max"),
    "action_recall": (0.80, "min"),
    "action_precision": (0.80, "min"),
    "assignee_accuracy": (0.90, "min"),
    "date_accuracy": (0.90, "min"),
    "ungrounded_rate": (0.20, "max"),
    "unflagged_wrong": (0, "max"),
}


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "").casefold()


def kw_score(text: str, keywords: list) -> float:
    """สัดส่วนคำสำคัญที่ปรากฏในข้อความ (ไม่สนช่องว่าง/ตัวพิมพ์)
    คำสำคัญหนึ่งตัวเป็นได้ทั้งข้อความเดียว หรือ "รายการคำพ้อง" (ปรากฏคำใดคำหนึ่งก็นับ) เช่น ["สองหมื่น", "20,000", "20000"]
    เพราะ AI เขียนตัวเลขได้หลายแบบแต่ความหมายเดียวกัน
    """
    if not keywords:
        return 0.0
    t = _squash(text)
    hits = 0
    for k in keywords:
        options = [k] if isinstance(k, str) else k
        hits += any(_squash(o) in t for o in options)
    return hits / len(keywords)


def _greedy_match(expected_scores: list[list[float]]) -> dict[int, int]:
    """จับคู่เฉลยกับรายการที่ AI สร้างแบบไม่ซ้ำ เลือกคู่ที่คะแนนสูงสุดก่อน (เฉพาะที่ผ่านเกณฑ์)
    expected_scores[i][j] = คะแนนของเฉลยที่ i กับรายการ AI ที่ j; คืน {i: j}
    """
    pairs = sorted(
        ((s, i, j) for i, row in enumerate(expected_scores) for j, s in enumerate(row) if s >= MATCH_THRESHOLD),
        reverse=True,
    )
    used_i, used_j, out = set(), set(), {}
    for _, i, j in pairs:
        if i not in used_i and j not in used_j:
            out[i] = j
            used_i.add(i)
            used_j.add(j)
    return out


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def evaluate(content: dict, expected: dict, participants: list[str] | None = None) -> dict:
    """ให้คะแนนรายงานหนึ่งฉบับ (content = ผลจาก summarizer ที่ผ่าน validate_minutes แล้ว) ค่า None = ไม่มีข้อมูลให้วัด"""
    exp_agenda = expected.get("agenda", [])
    exp_actions = expected.get("action_items", [])
    gen_agenda = content.get("agenda", [])
    gen_actions = content.get("action_items", [])

    # ── วาระ / มติ ──
    a_scores = [[kw_score(f"{g['title']} {g['discussion']}", e["topic"]) for g in gen_agenda] for e in exp_agenda]
    a_match = _greedy_match(a_scores)
    resolution_ok = 0
    for i, e in enumerate(exp_agenda):
        if i not in a_match:
            continue
        g = gen_agenda[a_match[i]]
        if e.get("resolution"):
            resolution_ok += bool(g.get("resolution")) and kw_score(g["resolution"], e["resolution"]) >= MATCH_THRESHOLD
        else:
            resolution_ok += not g.get("resolution")
    no_res_expected = [i for i, e in enumerate(exp_agenda) if not e.get("resolution") and i in a_match]
    false_res = sum(1 for i in no_res_expected if gen_agenda[a_match[i]].get("resolution"))

    # ── งาน ──
    t_scores = [[kw_score(g["description"], e["keywords"]) for g in gen_actions] for e in exp_actions]
    t_match = _greedy_match(t_scores)
    matched_gen = set(t_match.values())
    assignee_ok = date_ok = date_total = 0
    for i, j in t_match.items():
        e, g = exp_actions[i], gen_actions[j]
        assignee_ok += _squash(g.get("assignee") or "") == _squash(e.get("assignee") or "")
        if e.get("due_date"):
            date_total += 1
            date_ok += g.get("due_date") == e["due_date"]

    # ── ชั้นตรวจคุณภาพของระบบเอง ──
    checked = [g for g in gen_agenda if g.get("resolution")] + list(gen_actions)
    ungrounded = sum(1 for g in checked if g.get("grounded") is not True)
    names = {_squash(n) for n in (participants or [])}
    unflagged_wrong = sum(
        1 for j, g in enumerate(gen_actions)
        if j not in matched_gen and g.get("grounded") is True
        and (not names or _squash(g.get("assignee") or "") in names)
    )

    return {
        "agenda_recall": _ratio(len(a_match), len(exp_agenda)),
        "resolution_accuracy": _ratio(resolution_ok, len(exp_agenda)),
        "false_resolution_rate": _ratio(false_res, len(no_res_expected)),
        "action_recall": _ratio(len(t_match), len(exp_actions)),
        "action_precision": _ratio(len(matched_gen), len(gen_actions)) if gen_actions else (1.0 if not exp_actions else None),
        "assignee_accuracy": _ratio(assignee_ok, len(t_match)),
        "date_accuracy": _ratio(date_ok, date_total),
        "ungrounded_rate": _ratio(ungrounded, len(checked)),
        "unflagged_wrong": unflagged_wrong,
    }


def average(results: list[dict]) -> dict:
    """เฉลี่ยตัวชี้วัดข้ามหลายรอบ/หลายเคส ข้ามค่า None; unflagged_wrong เป็นผลรวม (นับเป็นจำนวนเหตุการณ์)"""
    out = {}
    for key in THRESHOLDS:
        vals = [r[key] for r in results if r.get(key) is not None]
        if not vals:
            out[key] = None
        elif key == "unflagged_wrong":
            out[key] = sum(vals)
        else:
            out[key] = sum(vals) / len(vals)
    return out


def verdict(metrics: dict) -> dict[str, str]:
    """ผ่าน/ไม่ผ่านรายตัวชี้วัดเทียบ THRESHOLDS ('-' = ไม่มีข้อมูลให้วัด)"""
    out = {}
    for key, (limit, kind) in THRESHOLDS.items():
        v = metrics.get(key)
        if v is None:
            out[key] = "-"
        else:
            out[key] = "ผ่าน" if (v >= limit if kind == "min" else v <= limit) else "ไม่ผ่าน"
    return out
