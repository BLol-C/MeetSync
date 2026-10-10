"""ส่วนกลางของหน้าเว็บ: ป้ายภาษาไทย, ตัวบอกขั้นตอน, ข้อความแจ้งผล, การนำทาง, แถบด้านข้าง"""

import re

import streamlit as st

import service

from bot import botclient
from reports import report_data

STATUS_LABEL = {
    "scheduled": "⚪ ตั้งค่าแล้ว รอเริ่มบอท",
    "recording": "🔴 กำลังบันทึก",
    "transcript_review": "🟠 รอตรวจ transcript",
    "transcript_verified": "🟡 รอให้ AI ร่างรายงาน",
    "draft": "🔵 รายงานฉบับร่าง",
    "approved": "🟢 อนุมัติแล้ว",
}

# สถานะ -> (หัวข้อสิ่งที่ต้องทำต่อ, คำอธิบาย) แสดงเป็นกล่อง "ขั้นตอนต่อไป" ที่หัวหน้าการประชุมทุกหน้า
NEXT_STEP = {
    "scheduled": ("เริ่มบอทเข้าห้องประชุม", "ตรวจรายชื่อผู้เข้าร่วมในแท็บ “① ข้อมูล” ให้ครบ แล้วไปแท็บ “② บอท” กด “เริ่มบอท”"),
    "recording": ("บอทกำลังบันทึกการประชุม", "เมื่อประชุมจบให้กด “หยุดบอท” ในแท็บ “② บอท”"),
    "transcript_review": ("ตรวจทานข้อความที่บอทจับได้",
                          "ไปแท็บ “③ Transcript” แก้ข้อความ/ชื่อผู้พูดที่ผิด รวมชื่อที่ซ้ำ แล้วกด “ยืนยัน transcript”"),
    "transcript_verified": ("ให้ AI ร่างรายงานการประชุม", "ไปแท็บ “④ รายงาน” แล้วกด “ให้ AI ร่างรายงาน”"),
    "draft": ("ตรวจ แก้ไข และอนุมัติรายงาน", "ไปแท็บ “④ รายงาน” แก้ให้ถูกต้อง (ดูรายการที่ควรตรวจ) แล้วกด “อนุมัติรายงาน”"),
    "approved": ("เสร็จแล้ว", "ดาวน์โหลด PDF หรือส่งงานที่มอบหมายเข้า Google Calendar ได้ที่แท็บ “④ รายงาน”"),
}

TAB_LABELS = ["① ข้อมูล", "② บอท", "③ Transcript", "④ รายงาน", "⑤ Calendar"]
_DEFAULT_TAB = {"scheduled": 0, "recording": 1, "transcript_review": 2, "transcript_verified": 3, "draft": 3, "approved": 4}


def default_tab(status: str) -> str:
    return TAB_LABELS[_DEFAULT_TAB.get(status, 0)]


ROLE_LABEL = {"chair": "ประธาน", "secretary": "เลขา", "attendee": "กรรมการ/สมาชิก", "guest": "ผู้เข้าร่วม (ไม่ใช่กรรมการ)"}
ROLE_BY_LABEL = {v: k for k, v in ROLE_LABEL.items()}
ATT_LABEL = {"invited": "ยังไม่ยืนยัน", "present": "เข้าร่วม", "absent": "ไม่มา"}
ATT_BY_LABEL = {v: k for k, v in ATT_LABEL.items()}
AGENDA_SECTION_LABEL = {
    "inform": "วาระ 1 · เรื่องแจ้งให้ที่ประชุมทราบ",
    "approve_prev": "วาระ 2 · รับรองรายงานการประชุมครั้งก่อน",
    "followup": "วาระ 3 · เรื่องสืบเนื่อง",
    "consider_old": "วาระ 4.1 · เรื่องค้างพิจารณา",
    "consider_new": "วาระ 4.2 · เรื่องพิจารณาใหม่",
}
AGENDA_SECTION_BY_LABEL = {v: k for k, v in AGENDA_SECTION_LABEL.items()}
SOURCE_LABEL = {"registered": "ลงทะเบียนไว้", "meet": "พบจาก Meet"}


def inject_css():
    st.markdown(
        """<style>
        .block-container { padding-top: 2.2rem; max-width: 1200px; }
        </style>""",
        unsafe_allow_html=True,
    )


def table_height(n_rows: int, *, max_px: int = 420, spare_rows: int = 0) -> int:
    """ความสูงตาราง (px) พอดีกับจำนวนแถว (+หัวตาราง +แถวว่างสำรอง) แต่ไม่เกิน max_px — เกินกว่านั้นเลื่อนดูภายในตาราง"""
    return min(max_px, 35 * (max(n_rows, 1) + 1 + spare_rows) + 3)


def clean_str(value) -> str:
    """ค่าจากตาราง (pandas) -> ข้อความที่ตัดช่องว่างแล้ว; None/NaN/NaT -> ''"""
    if value is None or value != value:
        return ""
    return str(value).strip()


def md_escape(text: str) -> str:
    """escape ตัวอักษรพิเศษของ Markdown/LaTeX ในข้อความภายนอก (ชื่อ/คำบรรยายจาก Meet) ก่อนแสดงผล"""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~$<&])", r"\\\1", text or "")




def previous_meeting_select(user: dict, *, key: str, current_id: int | None = None, exclude_id: int | None = None,
                            disabled: bool = False) -> int | None:
    """ช่องเลือก "การประชุมครั้งก่อน" (เฉพาะที่อนุมัติรายงานแล้ว) — ระบบเติมวาระ 2, 3, 4.1 ของรายงานจากครั้งนั้น คืน meeting_id หรือ None"""
    none_label = "— ไม่มี / ไม่ใช้ข้อมูลครั้งก่อน —"
    labels: dict[str, int | None] = {none_label: None}
    for m in service.previous_meeting_choices(user, exclude_id):
        no = f" · ครั้งที่ {m['meeting_no']}" if m.get("meeting_no") else ""
        labels[f"{m['title'] or '(ไม่ได้ตั้งชื่อ)'}{no} · {thai_date(m['started_at'])} (#{m['meeting_id']})"] = m["meeting_id"]
    ids = list(labels.values())
    wanted = current_id if current_id is not None and current_id in ids else None
    return labels[st.selectbox(
        "การประชุมครั้งก่อน", options=list(labels), index=ids.index(wanted), key=key, disabled=disabled,
        help="ระบบเติมรายงานวาระที่ 2 (รับรองรายงานครั้งก่อน) วาระที่ 3 (งานที่มอบหมายไว้) และวาระที่ 4.1 (เรื่องที่ไม่มีมติ) "
             "จากรายงานของการประชุมนี้ เลือกได้เฉพาะที่อนุมัติรายงานแล้ว — ตรวจและแก้ได้ที่แท็บ ④ ก่อนอนุมัติ")]


# ── นำทาง ──

def go(view: str = "home", meeting_id: int | None = None):
    """เปลี่ยนหน้าผ่าน query string (รีเฟรชแล้วอยู่หน้าเดิม ส่งลิงก์ให้คนอื่นเปิดตรงหน้านั้นได้)"""
    st.query_params.clear()
    if meeting_id is not None:
        st.query_params["m"] = str(meeting_id)
    elif view != "home":
        st.query_params["view"] = view
    st.rerun()


# ── ข้อความแจ้งผล (คงอยู่ข้ามการ rerun) ──

def flash(kind: str, text: str):
    st.session_state.setdefault("_flash", []).append((kind, text))


def show_flash():
    # ต้องมี container ตัวเดียวอยู่ที่ตำแหน่งเดิมทุกรอบ แม้ไม่มีข้อความ: ถ้าแบนเนอร์โผล่เป็น element ใหม่ด้านบนสุด
    # ตำแหน่งของทุกอย่างข้างล่างจะเลื่อน แล้วหน้าเก่าที่จางค้างซ้อนอยู่เหนือหน้าใหม่ (เจอจริงตอนกดหยุดบอท)
    with st.container():
        for kind, text in st.session_state.pop("_flash", []):
            getattr(st, kind)(text)


# ── ตัวบอกขั้นตอน ──

def progress_text(status: str) -> str:
    """สถานะ + ลำดับขั้นเป็นบรรทัดเดียว (เลขขั้นตรงกับแท็บ ①–④ — แท็บคือแถบนำทางเดียวของหน้านี้)"""
    label = STATUS_LABEL.get(status, status)
    if status == "approved":
        return label
    return f"{label}  ·  ขั้นที่ {_DEFAULT_TAB.get(status, 0) + 1} จาก {len(TAB_LABELS)}"


# ── แถบด้านข้าง ──

def render_sidebar(user: dict, dev: bool):
    with st.sidebar:
        st.markdown("## 🎙️ MeetSync")
        st.caption(f"{user.get('name') or ''}  \n{user.get('email') or ''}")
        if dev:
            st.warning("โหมดทดสอบ (ไม่ได้ล็อกอินจริง)")
        elif st.button("ออกจากระบบ", width="stretch"):
            st.logout()
        st.divider()
        if st.button("📋 การประชุมของฉัน", width="stretch"):
            go("home")
        if st.button("➕ สร้างการประชุมใหม่", width="stretch", type="primary"):
            go("new")
        st.divider()
        _bot_chip(user)


def _bot_chip(user: dict):
    st.caption("สถานะบอท")
    health = botclient.health()
    if health is None:
        st.error("บริการบอทไม่ได้เปิดอยู่  \nเปิดด้วย run.ps1", icon="⚠️")
        return
    if not health.get("db"):
        st.warning("บริการบอทต่อฐานข้อมูลไม่ได้")
        return
    try:
        state = botclient.state(user)
    except botclient.BotError as e:
        st.warning(str(e))
        return
    if state["running"] and state["mine"]:
        st.error("🔴 บอทกำลังบันทึกอยู่")
        if state["meeting_id"] and st.button("เปิดการประชุมที่กำลังบันทึก", width="stretch"):
            go("meeting", state["meeting_id"])
    elif state["running"]:
        st.warning("🟠 บอทกำลังบันทึกการประชุมของผู้อื่น")
    else:
        st.success("🟢 บอทว่าง")


def thai_date(d):
    return report_data.thai_date(d)
