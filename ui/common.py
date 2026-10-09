"""ส่วนกลางของหน้าเว็บ: ป้ายภาษาไทย, ตัวบอกขั้นตอน, ข้อความแจ้งผล, การนำทาง, แถบด้านข้าง"""

import re

import streamlit as st

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

STEPS = ["ตั้งค่า", "บันทึกด้วยบอท", "ตรวจ transcript", "ร่างและแก้รายงาน", "อนุมัติ"]
_STEP_OF = {"scheduled": 1, "recording": 1, "transcript_review": 2, "transcript_verified": 3, "draft": 3, "approved": 5}

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

TAB_LABELS = ["① ข้อมูล", "② บอท", "③ Transcript", "④ รายงาน"]
_DEFAULT_TAB = {"scheduled": 0, "recording": 1, "transcript_review": 2, "transcript_verified": 3, "draft": 3, "approved": 3}


def default_tab(status: str) -> str:
    return TAB_LABELS[_DEFAULT_TAB.get(status, 0)]


ROLE_LABEL = {"chair": "ประธาน", "secretary": "เลขา", "attendee": "กรรมการ/สมาชิก", "guest": "ผู้เข้าร่วม (ไม่ใช่กรรมการ)"}
ROLE_BY_LABEL = {v: k for k, v in ROLE_LABEL.items()}
ATT_LABEL = {"invited": "ยังไม่ยืนยัน", "present": "เข้าร่วม", "absent": "ไม่มา"}
ATT_BY_LABEL = {v: k for k, v in ATT_LABEL.items()}
SOURCE_LABEL = {"registered": "ลงทะเบียนไว้", "meet": "พบจาก Meet"}


def inject_css():
    st.markdown(
        """<style>
        .block-container { padding-top: 2.2rem; max-width: 1200px; }
        .ms-step { display:inline-block; padding:4px 12px; margin:0 6px 6px 0; border-radius:999px;
                   border:1px solid rgba(128,128,128,.35); font-size:.85rem; }
        .ms-step.done { border-color:#37c978; color:#37c978; }
        .ms-step.now { background:#4c8dff; border-color:#4c8dff; color:#fff; font-weight:600; }
        </style>""",
        unsafe_allow_html=True,
    )


def clean_str(value) -> str:
    """ค่าจากตาราง (pandas) -> ข้อความที่ตัดช่องว่างแล้ว; None/NaN/NaT -> ''"""
    if value is None or value != value:
        return ""
    return str(value).strip()


def md_escape(text: str) -> str:
    """escape ตัวอักษรพิเศษของ Markdown/LaTeX ในข้อความภายนอก (ชื่อ/คำบรรยายจาก Meet) ก่อนแสดงผล"""
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~$<&])", r"\\\1", text or "")


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

def render_stepper(status: str):
    now = _STEP_OF.get(status, 0)
    chips = []
    for i, name in enumerate(STEPS):
        cls = "done" if i < now else ("now" if i == now else "")
        mark = "✓ " if i < now else ""
        chips.append(f'<span class="ms-step {cls}">{i + 1}. {mark}{name}</span>')
    st.markdown("".join(chips), unsafe_allow_html=True)


def render_next_step(status: str):
    title, detail = NEXT_STEP.get(status, ("", ""))
    if status == "approved":
        st.success(f"**{title}** — {detail}")
    else:
        st.info(f"**ขั้นตอนต่อไป: {title}** — {detail}")


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
