"""ตัดคำไทยสำหรับ PDF: ภาษาไทยไม่เว้นวรรคระหว่างคำ fpdf2 ตัดบรรทัดได้เฉพาะที่ช่องว่าง จึงตัดข้อความไทยยาวๆ กลางคำ
(เช่น "...เกษตรศาสตร์วิท|ยาเขต...")

break_thai() แทรก ZERO WIDTH SPACE (U+200B) ระหว่างคำที่ตัดด้วย pythainlp (newmm) — fpdf2 ถือว่า ZWSP เป็นจุดที่ตัดบรรทัดได้
ฟอนต์ TH Sarabun New ไม่มีกลิฟนี้ แต่เรนเดอร์ไม่เห็นและกว้าง 0 (ตรวจแล้ว: ภาพ PDF ที่มี/ไม่มี ZWSP เหมือนกันทุกพิกเซล)
แต่ ZWSP จะติดเข้าไปในข้อความที่ค้นหา/คัดลอกจาก PDF ได้ จึงใช้เฉพาะตอนคำนวณจุดตัด แล้วลบออกก่อนวาดจริง (ดู _ReportPDF)
ถ้าไม่มี pythainlp จะคืนข้อความเดิมโดยไม่ทำอะไร (PDF ยังสร้างได้ แค่ตัดบรรทัดแบบเดิม)
"""

import functools
import re
import unicodedata

try:
    from pythainlp.tokenize import word_tokenize
except Exception:  # noqa: BLE001
    word_tokenize = None

ZWSP = chr(0x200B)
_THAI = re.compile("[" + chr(0x0E00) + "-" + chr(0x0E7F) + "]")
_NO_BREAK_BEFORE = chr(0x0E46) + chr(0x0E2F)     # ไม้ยมก ๆ / ไปยาลน้อย ฯ ต้องติดคำหน้า
_CHUNK_SPLIT = re.compile(r"(?<=\s)|(?<=" + ZWSP + ")")


def _is_thai_letter(ch: str) -> bool:
    return chr(0x0E00) <= ch <= chr(0x0E7F)


@functools.lru_cache(maxsize=2048)
def _break_run(run: str) -> str:
    tokens = [t for t in word_tokenize(run, engine="newmm", keep_whitespace=False) if t]
    out = [tokens[0]]
    for prev, tok in zip(tokens, tokens[1:]):
        # ตัดเฉพาะรอยต่อไทย-ไทย และไม่ตัดหน้าตัวที่ต้องติดคำหน้า / อักขระผสม (สระบน-ล่าง วรรณยุกต์)
        if (_is_thai_letter(prev[-1]) and _is_thai_letter(tok[0])
                and tok[0] not in _NO_BREAK_BEFORE and unicodedata.category(tok[0]) != "Mn"):
            out.append(ZWSP)
        out.append(tok)
    return "".join(out)


def break_thai(text):
    """แทรกจุดตัดบรรทัดที่รอยต่อคำไทย; ข้อความที่ไม่มีภาษาไทยหรือไม่ใช่ str คืนตามเดิม"""
    if not isinstance(text, str) or word_tokenize is None or not _THAI.search(text):
        return text
    parts = re.split(r"(\s+)", text)   # เก็บช่องว่าง/ขึ้นบรรทัดใหม่เดิมไว้ ตัดคำเฉพาะช่วงที่ไม่มีช่องว่าง
    return "".join(p if (not p or p.isspace() or not _THAI.search(p)) else _break_run(p) for p in parts)


def strip_breaks(text: str) -> str:
    return text.replace(ZWSP, "")


def wrap_chunks(text: str) -> list[str]:
    """แบ่งข้อความเป็นชิ้นที่ขึ้นบรรทัดใหม่ได้ระหว่างชิ้น (หลังช่องว่าง หรือที่รอยต่อคำไทย) โดยไม่มี ZWSP ในชิ้น
    ต่อชิ้นกลับกันได้ข้อความเดิม — ใช้กับ write() ที่ต้องรู้ตำแหน่งตัดเอง"""
    marked = break_thai(text)
    return [c.replace(ZWSP, "") for c in _CHUNK_SPLIT.split(marked) if c.replace(ZWSP, "")]
