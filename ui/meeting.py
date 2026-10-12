"""หน้าการประชุม: หัวเรื่อง + สถานะ/ขั้นที่ + 5 แท็บ

ทุกแท็บถูกวาดพร้อมกันทุกครั้ง (st.tabs) เพื่อให้ข้อมูลที่แก้ค้างไว้ในแท็บหนึ่งไม่หายตอนสลับไปแท็บอื่น
"""

import streamlit as st

from bot import botclient
import service
from service import ServiceError
from ui import common, tab_bot, tab_calendar, tab_report, tab_setup, tab_transcript


def _bot_info(user: dict, meeting_id: int) -> dict:
    """สถานะบอทที่ทุกแท็บใช้ร่วมกัน (เรียกบริการบอทครั้งเดียวต่อหนึ่งรอบ)"""
    info = {"reachable": False, "error": None, "running": False, "running_here": False, "running_other": False,
            "rows": [], "status": [], "bot_error": None}
    health = botclient.health()
    if health is None:
        info["error"] = "บริการบอทไม่ได้เปิดอยู่ — เปิดด้วย run.ps1 (หรือ uvicorn app:app) แล้วรีเฟรชหน้านี้"
        return info
    if not health.get("db"):
        info["error"] = "บริการบอทต่อฐานข้อมูลไม่ได้"
        return info
    try:
        state = botclient.state(user)
    except botclient.BotError as e:
        info["error"] = str(e)
        return info
    info["reachable"] = True
    info["running"] = state["running"]
    info["running_here"] = bool(state["running"] and state["mine"] and state["meeting_id"] == meeting_id)
    info["running_other"] = bool(state["running"] and not info["running_here"])
    info["rows"], info["status"], info["bot_error"] = state["rows"], state["status"], state["error"]
    return info


def render(user: dict, meeting_id: int):
    try:
        detail = service.get_detail(user, meeting_id)
    except ServiceError as e:
        st.error(str(e))
        if st.button("← กลับไปรายการประชุม"):
            common.go("home")
        return

    meeting = detail["meeting"]
    status = meeting["status"]
    detail["bot"] = _bot_info(user, meeting_id)

    top, rename, back = st.columns([6, 1.6, 1.6], vertical_alignment="center")
    top.title(common.md_escape(meeting["title"] or "การประชุม"))
    with rename.popover("✎ เปลี่ยนชื่อ", width="stretch", disabled=status == "approved",
                        help="รายงานที่อนุมัติแล้วเปลี่ยนชื่อไม่ได้ (ยกเลิกการอนุมัติก่อน)" if status == "approved" else None):
        new_title = st.text_input("ชื่อเรื่องการประชุม", value=meeting["title"] or "", key=f"title_{meeting_id}")
        if st.button("บันทึกชื่อ", key=f"title_save_{meeting_id}", type="primary"):
            try:
                service.update_meeting(user, meeting_id, title=new_title)
            except ServiceError as e:
                st.error(str(e))
            else:
                common.flash("success", "เปลี่ยนชื่อการประชุมแล้ว")
                st.rerun()
    if back.button("← รายการประชุม", width="stretch"):
        common.go("home")
    st.caption(common.progress_text(status))

    tab_key = f"tabs_{meeting_id}"
    tabs = st.tabs(common.TAB_LABELS, default=common.default_tab(status), key=tab_key)
    with tabs[0]:
        tab_setup.render(user, detail)
    with tabs[1]:
        tab_bot.render(user, detail)
    with tabs[2]:
        tab_transcript.render(user, detail)
    with tabs[3]:
        tab_report.render(user, detail)
    with tabs[4]:
        tab_calendar.render(user, detail)
