"""ล็อกอินด้วย Google (st.login แบบ OIDC ของ Streamlit) + เตรียมฐานข้อมูล

SA: ผู้ใช้ล็อกอินด้วยบัญชี Google ใดก็ได้ ขอสิทธิ์ระบุตัวตนอย่างเดียว (openid, email, profile) แยกจากสิทธิ์ Calendar
ซึ่งขอแยกต่างหากตอนกดเชื่อมต่อปฏิทิน — st.login ขอเฉพาะ openid/email/profile ตรงตามนั้น

โหมดทดสอบ: ตั้งตัวแปรสภาพแวดล้อม MEETSYNC_DEV_USER="อีเมล|ชื่อ" เพื่อข้ามการล็อกอินจริง (ใช้ในเทสต์/ถ่ายภาพหน้าจอเท่านั้น
มีป้ายเตือนแสดงตลอด) ห้ามตั้งค่านี้ตอนใช้งานจริง
"""

import json
import os

import streamlit as st

from bot import botclient
import db
import service
from ui import common

SETUP_HELP = """
**ยังไม่ได้ตั้งค่าการล็อกอิน Google** — ทำครั้งเดียว:

1. ใน Google Cloud Console (OAuth Client ตัวเดียวกับที่ใช้เชื่อม Calendar) เพิ่ม **Authorized redirect URI**:
   `http://localhost:8501/oauth2callback`
2. รันคำสั่งนี้ในโฟลเดอร์โปรเจกต์ (สร้างไฟล์ `.streamlit/secrets.toml` จาก `.env`):
   `venv\\Scripts\\python tools\\make_streamlit_secrets.py`
3. เปิดโปรแกรมใหม่ด้วย `run.ps1`
"""


@st.cache_resource(show_spinner="กำลังเตรียมฐานข้อมูล…")
def _init_db() -> bool:
    db.init_schema()
    return True


def ensure_db():
    try:
        _init_db()
    except Exception as e:  # noqa: BLE001
        st.error(f"เชื่อมต่อฐานข้อมูลไม่สำเร็จ: {e}")
        st.caption("ตรวจว่าเปิด MySQL อยู่ และค่า DB_* ในไฟล์ .env ถูกต้อง")
        st.stop()


def _dev_user() -> dict | None:
    raw = os.environ.get("MEETSYNC_DEV_USER", "").strip()
    if not raw:
        return None
    email, _, name = raw.partition("|")
    return {"sub": "dev:" + email, "email": email, "name": name or email, "picture": None}


def _claims() -> dict | None:
    dev = _dev_user()
    if dev:
        return dev
    if not getattr(st.user, "is_logged_in", False):
        return None
    get = st.user.get if hasattr(st.user, "get") else (lambda k, d=None: getattr(st.user, k, d))
    return {"sub": get("sub") or get("email"), "email": get("email"), "name": get("name"), "picture": get("picture")}


def current_user() -> dict | None:
    """ผู้ใช้ที่ล็อกอินอยู่ (พร้อม user_id ในฐานข้อมูล) หรือ None — บันทึกผู้ใช้/เวลาล็อกอินล่าสุดตาม SA Process 1"""
    claims = _claims()
    if not claims or not claims["email"]:
        return None
    cached = st.session_state.get("_user")
    if cached and cached["sub"] == claims["sub"]:
        return cached
    user_id = db.upsert_user(claims["sub"], claims["email"], claims["name"], claims["picture"])
    user = {**claims, "user_id": user_id}
    st.session_state["_user"] = user
    return user


def is_dev() -> bool:
    return _dev_user() is not None


def render_login():
    st.markdown("<div style='height: 22vh'></div>", unsafe_allow_html=True)       # ดันลงมากลางหน้าจอ
    st.markdown("<h1 style='text-align: center'>🎙️ MeetSync</h1>", unsafe_allow_html=True)
    try:
        configured = "auth" in st.secrets
    except Exception:  # noqa: BLE001 — ไม่มีไฟล์ secrets.toml
        configured = False
    if not configured:
        st.warning(SETUP_HELP)
        return
    _, middle, _ = st.columns([1, 1, 1])                                           # ปุ่มอยู่กึ่งกลางแนวนอน
    middle.button("เข้าสู่ระบบด้วย Google", type="primary", on_click=st.login, width="stretch")


def connect_calendar_on_first_login(user: dict):
    """ล็อกอินแล้วยังไม่เชื่อม Google Calendar: พาไปหน้าอนุญาตของ Google ต่อทันที (ไม่มีหน้าคั่น) กดอนุญาตหนึ่งครั้ง
    แล้ว Google ส่งกลับมาหน้าเดิม ทำอัตโนมัติ "ครั้งเดียวต่อเบราว์เซอร์ต่อผู้ใช้" (จำไว้ใน localStorage) กันวนซ้ำถ้าผู้ใช้กดปฏิเสธ
    — ปฏิเสธ/หมดอายุแล้วเชื่อมใหม่ได้ที่แท็บ ⑤ ข้ามในโหมดทดสอบ (ไม่มีบัญชี Google จริง)
    ใช้ JavaScript เปลี่ยนหน้า เพราะ Streamlit ไม่มีคำสั่ง redirect (ทดสอบแล้วว่า components.html / meta refresh ไม่ทำงาน)"""
    if is_dev() or service.calendar_connected(user):
        return
    url = botclient.calendar_connect_url(user, botclient.UI_URL)
    key = f"ms_cal_prompted_{user['user_id']}"
    st.html(
        "<script>try { const k = " + json.dumps(key) + "; if (!localStorage.getItem(k)) { localStorage.setItem(k, '1'); "
        "window.location.href = " + json.dumps(url) + "; } } catch (e) {}</script>",
        unsafe_allow_javascript=True,
    )


def require_login() -> dict:
    user = current_user()
    if user is None:
        render_login()
        st.stop()
    return user
