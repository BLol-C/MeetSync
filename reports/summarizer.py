"""
สร้าง "เนื้อหารายงานการประชุม" ด้วย Gemini จาก transcript ที่ตรวจทานแล้ว พร้อมชั้นตรวจคุณภาพหลัง AI

หลักการ: ไม่เชื่อผลของ AI ตรงๆ
  1. บังคับรูปแบบด้วย response_schema (pydantic) — ไม่ใช่แค่ขอ JSON ใน prompt
  2. ส่วนหัวรายงาน (วันที่ ประธาน เลขา ผู้เข้า/ไม่มา) มาจากข้อมูลในระบบ ไม่ให้ AI สร้าง
  3. หลัง AI ตอบ ทุกรายการผ่าน validate_minutes: ผู้รับผิดชอบต้องอยู่ในรายชื่อ, วันที่/เวลาต้องถูกต้อง,
     มติและงานต้องมี "ข้อความอ้างอิง" ที่หาเจอใน transcript จริง — ที่ไม่ผ่านจะถูกติดคำเตือนให้คนตรวจ
  4. prompt อยู่ในไฟล์ prompts/ (มีเวอร์ชัน) แก้ได้โดยไม่ต้องแตะโค้ด

ต้องตั้งค่า GEMINI_API_KEY ใน .env ก่อนใช้ (ขอฟรีได้ที่ https://aistudio.google.com/apikey)
"""

import datetime
import json
import os
import pathlib
import re
import time
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError

import db

HERE = pathlib.Path(__file__).resolve().parent.parent
PROMPT_DIR = HERE / "prompts"
PROMPT_NAME = os.environ.get("MINUTES_PROMPT", "minutes_v4")          # ชื่อไฟล์ใน prompts/ (ไม่รวม .md)
                                                                      # v2: ห้ามข้ามหัวข้อที่ไม่มีข้อสรุป + เขียนปี พ.ศ. ในเนื้อความ
                                                                      # v3: บันทึกการอภิปรายละเอียดแบบรายงานจริง (ใครรายงาน/ใครกล่าวอะไร) + AI เลือกหมวดวาระ
                                                                      #     + มติว่างเมื่อไม่มีใครสรุปชัดเจน (ห้ามเติม "รับทราบ" เอง)
                                                                      # v4: จากการทดสอบกับ Meet จริง — ห้ามเติม "รับทราบ" ให้เรื่องที่ไม่มีใครรับทราบ, บันทึกการกระทำของผู้พูดตรงตามจริง,
                                                                      #     ห้ามเสริมรายละเอียด, คำที่ถอดเสียงผิดต้องแก้ให้ตรงกันทุกจุด
                                                                      # (v1/v2 ยังเก็บไว้ให้ไล่ย้อนรายงานที่เคยสร้างด้วยเวอร์ชันเก่าได้)
MAP_PROMPT_NAME = os.environ.get("MINUTES_MAP_PROMPT", "minutes_map_v2")

_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
_MAX_RETRIES = 4
_RETRY_DELAY_SEC = 5            # รอ 5, 10, 20 วินาที (ทวีคูณ) ระหว่างความพยายามที่ 1->2->3->4 ตอน Google โหลดสูงชั่วคราว
_RATE_LIMIT_DELAY_SEC = 15
_REQUEST_TIMEOUT_MS = 120_000   # รายงานยาว + ประชุมนาน ใช้เวลาตอบมากกว่าสรุปสั้นๆ แบบเดิม

# transcript ยาวกว่านี้ (ตัวอักษร) จะใช้ map-reduce: สกัดบันทึกย่อทีละช่วงก่อน แล้วค่อยเรียบเรียงรายงาน
SINGLE_PASS_CHARS = int(os.environ.get("MINUTES_SINGLE_PASS_CHARS", "60000"))
CHUNK_CHARS = int(os.environ.get("MINUTES_CHUNK_CHARS", "30000"))

# วาระที่ AI สกัดจาก transcript จึงต้องมีหลักฐานอ้างอิง — วาระที่ระบบเติมจากครั้งก่อน/คนเพิ่มเองไม่ต้องตรวจหลักฐาน
GROUNDED_SECTIONS = ("inform", "consider_new")
GROUNDED_THRESHOLD = 0.8   # สัดส่วนของข้อความอ้างอิงที่ต้องหาเจอใน transcript ถึงถือว่า "มีที่มาจริง"

_THAI_WEEKDAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
_ROLE_LABEL = {"chair": "ประธาน", "secretary": "เลขา", "attendee": "กรรมการ/สมาชิก", "guest": "ผู้เข้าร่วม (ไม่ใช่กรรมการ)"}


# ── schema ที่บังคับให้ Gemini ตอบ ──

class AgendaItemAI(BaseModel):
    section: Literal["inform", "consider_new"]    # AI เลือกได้เฉพาะสองหมวดนี้ ส่วนวาระ 2, 3, 4.1 ระบบเติมจากการประชุมครั้งก่อน
    title: str
    discussion: str
    resolution: str | None
    evidence: list[str]


class ActionItemAI(BaseModel):
    description: str
    assignee: str | None
    due_date: str | None
    due_time: str | None
    due_time_end: str | None
    evidence: list[str]


class MinutesBodyAI(BaseModel):
    summary: str
    agenda: list[AgendaItemAI]
    other_matters: str | None
    action_items: list[ActionItemAI]


# ── ตรวจคุณภาพหลัง AI (ฟังก์ชันล้วน ไม่แตะ DB/เครือข่าย ทดสอบได้ตรงๆ) ──

# ตัวเลขหลังคำประมาณค่าที่ไม่มีหน่วยตามหลัง เช่น "เสร็จไปแล้วประมาณ 1 ยังเหลือ…" (คำบรรยายสดมักถอด "ครึ่งหนึ่ง" เป็น "1")
# อ่านแล้วไม่เป็นประโยค — ไม่เดาแก้ให้ แต่เตือนให้คนตรวจกับ transcript
_APPROX_NUMBER_RE = re.compile(r"(ประมาณ|ราวๆ|ราว|เกือบ|กว่า)\s*([0-9๐-๙][0-9๐-๙,.]*)\s*(?P<rest>[^\n]{0,12})")
_NUMBER_UNITS = (
    "%", "บาท", "เครื่อง", "คน", "ท่าน", "ครั้ง", "วัน", "สัปดาห์", "เดือน", "ปี", "ชั่วโมง", "ชม", "นาที", "วินาที", "ชิ้น", "อัน",
    "ห้อง", "โครงการ", "ร้าน", "แห่ง", "ราย", "รายการ", "หน้า", "เรื่อง", "ข้อ", "ชุด", "ตัว", "คัน", "หลัง", "ชั้น", "เมตร",
    "กิโลเมตร", "ตารางเมตร", "กิโลกรัม", "กรัม", "ไร่", "เปอร์เซ็นต์", "เปอร์เซนต์", "ล้าน", "พัน", "หมื่น", "แสน", "ร้อย", "เท่า",
    "เหรียญ", "ดอลลาร์", "หน่วย", "กลุ่ม", "ฝ่าย", "ทีม", "ระบบ", "ประเภท", "ด้าน", "ช่วง", "รอบ", "งวด", "แผ่น", "เล่ม", "ลำดับ",
)


def find_unclear_number(text: str | None) -> str | None:
    """คืนข้อความสั้นๆ รอบตัวเลขที่อ่านแล้วไม่เป็นประโยค (ประมาณ/ราว/เกือบ/กว่า + ตัวเลขเปล่าไม่มีหน่วย) หรือ None ถ้าไม่พบ"""
    for m in _APPROX_NUMBER_RE.finditer(text or ""):
        rest = m.group("rest")
        if rest.startswith(_NUMBER_UNITS):
            continue
        return (m.group(1) + " " + m.group(2) + (" " + rest if rest else "")).strip()
    return None


def _squash(s: str) -> str:
    """ตัดช่องว่างทั้งหมดทิ้งก่อนเทียบข้อความ: ASR ภาษาไทยเว้นวรรคไม่แน่นอน ('สวัสดี ครับ' = 'สวัสดีครับ')"""
    return re.sub(r"\s+", "", s or "")


def _shingles(s: str, k: int = 4) -> set[str]:
    s = _squash(s)
    if not s:
        return set()
    return {s[i:i + k] for i in range(max(len(s) - k + 1, 1))}


def transcript_shingles(texts: list[str]) -> set[str]:
    return _shingles("".join(_squash(t) for t in texts))


def grounding_score(quote: str, source_shingles: set[str]) -> float:
    """สัดส่วน (0-1) ของข้อความอ้างอิงที่พบใน transcript จริง — ทนต่อการเว้นวรรค/คำพลาดเล็กน้อย
    แต่ข้อความที่ AI แต่งขึ้นเองจะได้คะแนนต่ำ
    """
    sh = _shingles(quote)
    if not sh:
        return 0.0
    return len(sh & source_shingles) / len(sh)


_TITLE_RE = re.compile(r"^(คุณ|นางสาว|นาง|นาย|น\.ส\.|ดร\.|ผศ\.|รศ\.|ศ\.|อาจารย์|อ\.|mr\.?|mrs\.?|ms\.?|dr\.?)\s*", re.I)


def _name_key(name: str) -> str:
    """ชื่อสำหรับเทียบ: ตัดช่องว่าง/ตัวพิมพ์/ต่อท้าย (You) และคำนำหน้าชื่อ (คุณ นาย นาง ดร. ฯลฯ) ที่ AI มักใส่มาให้"""
    n = db.normalize_name(name or "")
    stripped = _TITLE_RE.sub("", n)
    return stripped or n


def match_participant_name(name: str | None, participants: list[dict]) -> str | None:
    """ชื่อผู้รับผิดชอบที่ AI ให้มา -> ชื่อในรายชื่อผู้เข้าร่วม (ตรงตัว หรือเป็นส่วนหนึ่งของกันและกันแบบไม่กำกวม)
    ถ้าจับคู่ได้มากกว่าหนึ่งคนจะไม่เดา (คืน None) ให้คนเลือกเอง
    """
    n = _name_key(name or "")
    if not n:
        return None
    exact = [p for p in participants if _name_key(p["display_name"]) == n]
    if len(exact) == 1:
        return exact[0]["display_name"]
    partial = []
    for p in participants:
        pn = _name_key(p["display_name"])
        if len(pn) >= 2 and len(n) >= 2 and (pn in n or n in pn):
            partial.append(p["display_name"])
    return partial[0] if len(partial) == 1 else None


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _norm_time(value) -> str | None:
    if not value:
        return None
    m = _TIME_RE.match(str(value).strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else "INVALID"


def _clean_str(v) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else v
    return v or None


def _clean_evidence(v) -> list[str]:
    return [e.strip() for e in (v or []) if isinstance(e, str) and e.strip()]


def validate_minutes(
    content: dict,
    participants: list[dict],
    source_texts: list[str],
    meeting_date: datetime.date | None,
) -> dict:
    """ทำความสะอาดเนื้อหารายงานและตรวจคุณภาพ คืนเนื้อหาใหม่ที่มี "warnings" (ว่าง = ผ่านทุกข้อ)

    ใช้ซ้ำได้หลังคนแก้ไข (คำนวณคำเตือนใหม่จากเนื้อหาปัจจุบัน) — ไม่แก้ค่าที่คนตั้งใจใส่ ยกเว้นล้างวันที่/เวลาที่อ่านไม่ได้
    """
    warnings: list[dict] = []

    def warn(code: str, path: str, message: str):
        warnings.append({"code": code, "path": path, "message": message})

    shingles = transcript_shingles(source_texts)

    def check_evidence(path: str, evidence: list[str], required: bool, what: str):
        if not evidence:
            if required:
                warn("no_evidence", path, f"{what}ไม่มีข้อความอ้างอิงจาก transcript — ตรวจสอบว่าเกิดขึ้นจริง")
            return None
        scores = [grounding_score(e, shingles) for e in evidence]
        ok = all(s >= GROUNDED_THRESHOLD for s in scores)
        if not ok:
            warn("ungrounded", path, f"{what}อ้างอิงข้อความที่หาไม่เจอใน transcript (อาจเป็นข้อมูลที่ AI แต่งขึ้น)")
        return ok

    def check_numbers(path: str, text: str | None, where: str):
        hit = find_unclear_number(text)
        if hit:
            warn("unclear_number", path, f"{where}: มีตัวเลขที่อ่านแล้วไม่เป็นประโยค (“…{hit}…”) อาจเป็นการถอดเสียงผิด — ตรวจกับ transcript แล้วแก้")

    summary = _clean_str(content.get("summary")) or ""
    check_numbers("summary", summary, "สรุปภาพรวม")
    if not summary:
        warn("empty_summary", "summary", "ไม่มีสรุปภาพรวมการประชุม")

    agenda = []
    for i, a in enumerate(content.get("agenda") or []):
        item = {
            "section": a.get("section") if a.get("section") in db.AGENDA_SECTIONS else db.DEFAULT_AGENDA_SECTION,
            "title": _clean_str(a.get("title")) or "",
            "discussion": _clean_str(a.get("discussion")) or "",
            "resolution": _clean_str(a.get("resolution")),
            "evidence": _clean_evidence(a.get("evidence")),
        }
        if not item["title"]:
            warn("agenda_title_missing", f"agenda[{i}].title", f"เรื่องที่ {i + 1} ไม่มีชื่อ")
        for field in ("title", "discussion", "resolution"):
            check_numbers(f"agenda[{i}].{field}", item[field], f"เรื่องที่ {i + 1}")
        if item["section"] in GROUNDED_SECTIONS:
            item["grounded"] = check_evidence(
                f"agenda[{i}].resolution", item["evidence"], required=bool(item["resolution"]),
                what=f"มติของเรื่องที่ {i + 1} ",
            )
        else:
            item["grounded"] = None
        agenda.append(item)
    if not any(a["section"] in GROUNDED_SECTIONS for a in agenda):
        warn("no_agenda", "agenda", "ไม่พบวาระการประชุมที่ AI สกัดได้ — ตรวจสอบ transcript หรือเพิ่มวาระเอง")

    actions = []
    for i, a in enumerate(content.get("action_items") or []):
        item = {
            "description": _clean_str(a.get("description")) or "",
            "assignee": _clean_str(a.get("assignee")),
            "due_date": _clean_str(a.get("due_date")),
            "due_time": _norm_time(a.get("due_time")),
            "due_time_end": _norm_time(a.get("due_time_end")),
            "evidence": _clean_evidence(a.get("evidence")),
        }
        for key in ("action_item_id", "calendar_synced", "google_calendar_event_id", "google_calendar_link"):   # ข้อมูลของแถวที่มีอยู่แล้ว ส่งต่อไว้
            if key in a:
                item[key] = a[key]
        label = f"งานที่ {i + 1}"
        check_numbers(f"action_items[{i}].description", item["description"], label)
        if not item["description"]:
            warn("action_description_missing", f"action_items[{i}].description", f"{label}ไม่มีรายละเอียด")

        if item["assignee"]:
            matched = match_participant_name(item["assignee"], participants)
            if matched:
                item["assignee"] = matched
            else:
                warn("assignee_unknown", f"action_items[{i}].assignee",
                     f"{label}: ผู้รับผิดชอบ \"{item['assignee']}\" ไม่อยู่ในรายชื่อผู้เข้าร่วม")
        else:
            warn("assignee_missing", f"action_items[{i}].assignee", f"{label}ยังไม่ระบุผู้รับผิดชอบ")

        if item["due_date"]:
            try:
                d = datetime.date.fromisoformat(item["due_date"])
                if meeting_date and d < meeting_date:
                    warn("date_before_meeting", f"action_items[{i}].due_date",
                         f"{label}: กำหนดส่ง {d} อยู่ก่อนวันประชุม {meeting_date}")
            except ValueError:
                warn("bad_date", f"action_items[{i}].due_date",
                     f"{label}: วันที่ \"{item['due_date']}\" อ่านไม่ได้ จึงล้างค่าให้ (ระบุใหม่ด้วยตนเอง)")
                item["due_date"] = None
        for key, label_t in (("due_time", "เวลา"), ("due_time_end", "เวลาสิ้นสุด")):
            if item[key] == "INVALID":
                warn("bad_time", f"action_items[{i}].{key}", f"{label}: {label_t}อ่านไม่ได้ จึงล้างค่าให้")
                item[key] = None
        if item["due_time"] and item["due_time_end"] and item["due_time_end"] <= item["due_time"]:
            warn("time_order", f"action_items[{i}].due_time_end", f"{label}: เวลาสิ้นสุดไม่หลังเวลาเริ่ม")
        if (item["due_time"] or item["due_time_end"]) and not item["due_date"]:
            warn("time_without_date", f"action_items[{i}].due_date", f"{label}: มีเวลาแต่ไม่มีวันที่")

        item["grounded"] = check_evidence(
            f"action_items[{i}].evidence", item["evidence"], required=True, what=f"{label} "
        )
        actions.append(item)

    return {
        "summary": summary,
        "agenda": agenda,
        "other_matters": _clean_str(content.get("other_matters")),
        "action_items": actions,
        "warnings": warnings,
    }


# ── เตรียม prompt / แบ่ง transcript ──

def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def fill(template: str, **values: str) -> str:
    """แทนที่ {ชื่อ} ในครั้งเดียว (ไม่ใช้ str.format เพราะ transcript อาจมีวงเล็บปีกกา)
    ข้อความที่ถูกใส่เข้าไปจะไม่ถูกแทนซ้ำ — เช่น ชื่อผู้เข้าร่วมที่เป็น "{transcript}" ไม่ทำให้ transcript ทะลักมาโผล่ในรายชื่อ
    """
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def format_participants(participants: list[dict]) -> str:
    if not participants:
        return "(ไม่ได้ระบุรายชื่อผู้เข้าร่วม — ให้ assignee เป็น null ทุกรายการ)"
    return "\n".join(f"- {p['display_name']} ({_ROLE_LABEL.get(p['role'], p['role'])})" for p in participants)


def format_transcript(rows: list[dict]) -> str:
    return "\n".join(f"[{r['spoken_at']:%H:%M}] {r['display_name']}: {r['text']}" for r in rows)


def split_chunks(rows: list[dict], limit: int) -> list[list[dict]]:
    """แบ่งรายการช่วงคำพูดเป็นก้อนๆ ไม่เกิน limit ตัวอักษร ตัดที่ขอบของช่วงคำพูดเท่านั้น (ไม่ตัดกลางประโยค)"""
    chunks, cur, size = [], [], 0
    for r in rows:
        n = len(r["text"]) + len(r["display_name"]) + 12
        if cur and size + n > limit:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


# ── เรียก Gemini ──

def _gemini_generate() -> Callable:
    """คืนฟังก์ชัน generate(prompt, schema|None) -> ข้อความที่ Gemini ตอบ (พร้อม retry)"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("ยังไม่ได้ตั้งค่า GEMINI_API_KEY ใน .env")
    from google import genai
    from google.genai import errors, types

    # SDK มี retry ในตัวอยู่แล้ว ปิดไว้เพื่อไม่ให้ซ้อนกับ retry ของเราด้านล่าง (เดิมรอเป็นนาทีตอน Google โหลดสูง)
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=_REQUEST_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )

    def generate(prompt: str, schema=None) -> str:
        if schema is not None:
            config = types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema)
        else:
            config = types.GenerateContentConfig()
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return client.models.generate_content(model=_MODEL, contents=prompt, config=config).text
            except errors.ServerError:       # 5xx: ฝั่ง Google โหลดสูงชั่วคราว
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(_RETRY_DELAY_SEC * 2 ** (attempt - 1))
            except errors.ClientError as e:  # 429 โควต้า/ความถี่ รอแล้วลองใหม่ได้ ส่วน 4xx อื่นๆ ผิดที่เราเอง ไม่ลองซ้ำ
                if getattr(e, "code", None) != 429 or attempt == _MAX_RETRIES:
                    raise
                time.sleep(_RATE_LIMIT_DELAY_SEC * attempt)

    return generate


def _parse_body(raw: str) -> MinutesBodyAI:
    return MinutesBodyAI.model_validate_json(raw)


def generate_minutes_content(
    rows: list[dict],
    participants: list[dict],
    meeting_title: str | None,
    meeting_date: datetime.datetime,
    generate: Callable | None = None,
) -> dict:
    """transcript ที่ตรวจแล้ว -> เนื้อหารายงานที่ผ่าน validate_minutes แล้ว (มี warnings)

    generate ฉีดเข้ามาได้ (ไว้ทดสอบโดยไม่เรียก Gemini จริง) ค่าเริ่มต้นคือ Gemini
    """
    if not rows:
        raise ValueError("ไม่มี transcript สำหรับการประชุมนี้ (หรือถูกลบหมดแล้ว)")
    generate = generate or _gemini_generate()

    transcript = format_transcript(rows)
    date_str = meeting_date.strftime("%Y-%m-%d")
    parts_text = format_participants(participants)

    if len(transcript) <= SINGLE_PASS_CHARS:
        source_label, source_title, source = "transcript", "Transcript", transcript
    else:
        # ประชุมยาว: สกัดบันทึกย่อทีละช่วงก่อน (ข้อความอ้างอิงในบันทึกคัดลอกตรงตัว) แล้วเรียบเรียงจากบันทึกทั้งหมด
        chunks = split_chunks(rows, CHUNK_CHARS)
        map_tpl = load_prompt(MAP_PROMPT_NAME)
        notes = []
        for i, chunk in enumerate(chunks, start=1):
            notes.append(f"=== ช่วงที่ {i}/{len(chunks)} ===\n" + generate(fill(
                map_tpl, part=str(i), total=str(len(chunks)), date=date_str,
                participants=parts_text, transcript=format_transcript(chunk),
            )))
        source_label, source_title, source = (
            "บันทึกย่อ", "บันทึกย่อที่สกัดจากทุกช่วงของ transcript (เรียงตามเวลา ข้อความอ้างอิงในบันทึกคัดลอกมาตรงตัวจาก transcript)",
            "\n\n".join(notes),
        )

    prompt = fill(
        load_prompt(PROMPT_NAME),
        meeting_title=meeting_title or "(ไม่ได้ระบุ)",
        weekday=_THAI_WEEKDAYS[meeting_date.weekday()],
        date=date_str,
        participants=parts_text,
        source_label=source_label,
        source_title=source_title,
        transcript=source,
    )

    body = None
    for attempt in (1, 2):   # AI ตอบนอก schema (หายาก) ลองใหม่หนึ่งครั้งก่อนยอมแพ้
        try:
            body = _parse_body(generate(prompt, MinutesBodyAI))
            break
        except (ValidationError, json.JSONDecodeError, TypeError):
            if attempt == 2:
                raise ValueError("AI ตอบกลับรูปแบบที่ไม่ถูกต้อง ลองสร้างรายงานใหม่อีกครั้ง")
    return validate_minutes(
        body.model_dump(), participants, [r["text"] for r in rows], meeting_date.date()
    )


# ── ประกอบเข้ากับ DB ──

def for_storage(content: dict) -> dict:
    """เนื้อหาเฉพาะส่วนที่เก็บลงฐานข้อมูล (คำเตือนและเครื่องหมาย grounded คำนวณสดทุกครั้ง ไม่เก็บ)"""
    return {
        "summary": content.get("summary") or "",
        "other_matters": content.get("other_matters"),
        "agenda": sorted(     # เรียงตามหมวดวาระ (คงลำดับเดิมในหมวดเดียวกัน) ให้ตรงกับที่แสดงในรายงาน
            ({k: a.get(k) for k in ("section", "title", "discussion", "resolution", "evidence")}
             for a in content.get("agenda") or []),
            key=lambda a: db.AGENDA_SECTIONS.index(a["section"]) if a.get("section") in db.AGENDA_SECTIONS else len(db.AGENDA_SECTIONS),
        ),
        "action_items": [
            {k: it.get(k) for k in ("description", "assignee", "due_date", "due_time", "due_time_end", "evidence")}
            for it in content.get("action_items") or []
        ],
    }


def generate_for_meeting(meeting_id: int, generate: Callable | None = None) -> dict:
    """ให้ AI ร่างรายงานของการประชุม แล้วเลื่อนสถานะเป็น draft — ถ้ามีฉบับร่างอยู่แล้วจะถูกแทนที่ (รายงาน 1 ฉบับต่อ 1 ประชุม
    ตาม SA) รายงานที่อนุมัติแล้วเขียนทับไม่ได้ ต้องยกเลิกการอนุมัติ (db.reopen_report) ก่อน
    ต้องยืนยัน transcript แล้วเท่านั้น (transcript_verified) หรือกำลังแก้ร่างอยู่ (draft)
    """
    meeting = db.get_meeting(meeting_id)
    if not meeting:
        raise ValueError("ไม่พบการประชุมนี้")
    if meeting["status"] not in ("transcript_verified", "draft"):
        raise ValueError("ต้องตรวจทานและกด \"ยืนยัน transcript\" ก่อน จึงจะให้ AI สร้างรายงานได้")

    rows = db.get_transcript(meeting_id)
    participants = db.list_speakers(meeting_id)
    content = generate_minutes_content(
        rows, participants, meeting.get("title"),
        meeting.get("started_at") or datetime.datetime.now(), generate,
    )
    stored = for_storage(content)
    from reports import continuity   # เติมวาระ 2, 3, 4.1 จากการประชุมครั้งก่อนที่เลือกไว้ (ถ้ามี) — ไม่ผ่าน AI
    full = for_storage({**stored, "agenda": stored["agenda"] + continuity.prefill_items(meeting)})
    saved = db.save_report(meeting_id, full, _MODEL, PROMPT_NAME, ai_snapshot=stored)   # ai_snapshot = เฉพาะที่ AI ร่าง
    db.set_meeting_status(meeting_id, "draft")
    return {**saved, "content": content}
