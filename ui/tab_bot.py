"""แท็บ ② บอท: เริ่ม/หยุดบอท และดูคำบรรยายสด"""

import streamlit as st

from bot import botclient
from ui import common


def _start(user: dict, mid: int):
    try:
        botclient.start(user, mid)
    except botclient.BotError as e:
        st.error(str(e))
        return
    st.rerun()


def _stop(user: dict):
    st.session_state["_bot_stopping"] = True
    try:
        botclient.stop(user)
    except botclient.BotError as e:
        st.session_state.pop("_bot_stopping", None)
        st.error(str(e))
        return
    common.flash("success", "หยุดบอทแล้ว — ไปตรวจทาน transcript ที่แท็บ ③ ได้เลย")
    st.rerun()


def _live_panel(user: dict, mid: int):
    """คำบรรยายสด: รีเฟรชเฉพาะกล่องนี้ทุก 2 วินาที (ไม่ทำให้ทั้งหน้ารีโหลด และไม่ทำให้ข้อความที่แก้ค้างอยู่ในแท็บอื่นหาย)"""

    @st.fragment(run_every=2)
    def panel():
        try:
            state = botclient.state(user)
        except botclient.BotError as e:
            st.warning(str(e))
            return
        if not (state["running"] and state["meeting_id"] == mid):
            # บอทหยุดแล้ว (หรือจบเอง) -> รีเฟรชทั้งหน้าให้สถานะ/ขั้นตอนอัปเดต
            # แต่ถ้าผู้ใช้เพิ่งกดหยุดเอง ปุ่มสั่ง rerun อยู่แล้ว — สอง rerun ชนกันทำให้หน้าเก่าที่จางค้างซ้อนบนหน้าใหม่
            if not st.session_state.get("_bot_stopping"):
                st.rerun()
            return
        for line in state["status"][-3:]:
            st.caption(f"{line['t']}  {line['text']}")
        if state["error"]:
            st.error(state["error"])
        rows = state["rows"][-60:][::-1]      # ล่าสุดอยู่บนสุด ไม่ต้องเลื่อนตาม
        with st.container(height=420, border=True):
            if not rows:
                st.caption("ยังไม่มีคำบรรยาย — รอบอทเข้าห้องและเปิด CC (ถ้าอยู่ในห้องรอ ให้ผู้จัดกดอนุญาตให้บอทเข้า)")
            for r in rows:
                name, text = common.md_escape(r["name"]), common.md_escape(r["text"])
                st.markdown(f"**{name}:** {text}" if r["final"] else f":gray[**{name}:** {text}]")

    panel()


def render(user: dict, detail: dict):
    meeting = detail["meeting"]
    mid = meeting["meeting_id"]
    status = meeting["status"]
    bot = detail["bot"]

    if not bot["running_here"]:
        st.session_state.pop("_bot_stopping", None)   # หน้านี้ไม่มีกล่องสดแล้ว เคลียร์ธงที่ใช้กัน rerun ชน
    st.subheader("บอทเข้าห้องประชุม")
    if bot["error"]:
        st.error(bot["error"])

    if bot["running_here"]:
        if st.button("⏹ หยุดบอท (ประชุมจบแล้ว)", key=f"stop_{mid}", type="primary"):
            _stop(user)
        _live_panel(user, mid)
        return

    if bot["running_other"]:
        st.warning("บอทกำลังบันทึกการประชุมอื่นอยู่ — บอทรับได้ทีละห้อง หยุดอันนั้นก่อนแล้วค่อยมาเริ่มที่นี่")

    if status in ("scheduled", "recording", "transcript_review") and bot["reachable"] and not bot["running_other"]:
        if status == "scheduled":
            label = "▶ เริ่มบอทเข้าห้องประชุม"
        else:
            if status == "recording":
                st.warning("การประชุมนี้ยังอยู่ในสถานะ “กำลังบันทึก” แต่บอทไม่ได้ทำงานอยู่ (บอทอาจหลุดกลางประชุม) "
                           "กดบันทึกต่อได้ ข้อความที่จับได้แล้วไม่หาย")
            else:
                st.caption("ถ้าการประชุมยังไม่จบ กดบันทึกต่อได้ ข้อความเดิมคงอยู่ และตัวเลขลำดับต่อจากเดิม")
            label = "▶ กลับไปบันทึกต่อ"
        if st.button(label, key=f"start_{mid}", type="primary"):
            _start(user, mid)
    elif status in ("transcript_verified", "draft", "approved"):
        st.info("การบันทึกการประชุมนี้เสร็จสิ้นแล้ว (ผ่านขั้นตรวจทาน transcript ไปแล้ว)")
