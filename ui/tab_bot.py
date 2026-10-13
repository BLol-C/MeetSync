"""แท็บ ② บอท: เริ่ม/หยุดบอท และดูคำบรรยายสด"""

import streamlit as st

import service
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


def duration_text(seconds: int) -> str:
    """เวลาที่อยู่ในห้อง รูปแบบ "0 ชั่วโมง 0 นาที 0 วินาที" """
    seconds = max(0, int(seconds))
    return f"{seconds // 3600} ชั่วโมง {seconds % 3600 // 60} นาที {seconds % 60} วินาที"


def bot_state_text(state: dict) -> str:
    """สถานะบอทสั้นๆ บรรทัดเดียว: กำลังเข้าห้อง / กำลังรอเข้าห้อง (รอผู้จัดกดอนุญาต) / กำลังฟังอยู่
    (ไม่แสดงข้อความดิบจากบันทึกของบอท — คำเตือนจริงแสดงแยกในกล่องเหลือง)"""
    texts = [line["text"] for line in state["status"]]
    joined = (state.get("in_room_seconds") is not None or bool(state["rows"])
              or any("✅" in t or "กำลังฟังคำบรรยาย" in t for t in texts))
    if joined:
        for t in reversed(texts):                 # เข้าห้องแล้วแต่เปิดคำบรรยายไม่ได้ = ยังไม่ได้ฟังจริง
            if "ยังเปิดคำบรรยายไม่สำเร็จ" in t or t.startswith("⚠️ เปิดคำบรรยายไม่สำเร็จ"):
                return "บอทอยู่ในห้องแล้ว แต่ยังฟังไม่ได้ (เปิดคำบรรยายไม่สำเร็จ)"
            if "เปิดคำบรรยายแล้ว" in t or "กำลังฟังคำบรรยาย" in t:
                break
        return "บอทกำลังฟังอยู่"
    for t in reversed(texts):
        if "ส่งคำขอเข้าร่วมแล้ว" in t:
            return "บอทกำลังรอเข้าห้อง"
        if "กดเข้าร่วมแล้ว" in t or "กำลังตรวจว่าเข้าห้อง" in t:
            break
    return "บอทกำลังเข้าห้อง"


def _live_panel(user: dict, mid: int):
    """คำบรรยายสด: รีเฟรชเฉพาะกล่องนี้ทุก 1 วินาที (ตัวนับเวลาที่อยู่ในห้องขยับทีละวินาที) (ไม่ทำให้ทั้งหน้ารีโหลด และไม่ทำให้ข้อความที่แก้ค้างอยู่ในแท็บอื่นหาย)"""

    @st.fragment(run_every=1)
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
        left, right = st.columns([3, 2], vertical_alignment="center")
        left.markdown(f"**{bot_state_text(state)}**")
        if state.get("in_room_seconds") is not None:
            right.markdown(f"<div style='text-align:right'><b>อยู่ในห้องมาแล้ว {duration_text(state['in_room_seconds'])}</b></div>",
                           unsafe_allow_html=True)
        outsiders = service.outsider_names(user, mid)
        if outsiders:
            st.warning("มีชื่อในห้องที่ไม่อยู่ในรายชื่อ: " + ", ".join(outsiders)
                       + " — ถ้าไม่ได้เชิญ เชิญออกจากห้อง Meet ได้ (หรือเป็นชื่อที่สะกดต่างจากรายชื่อ) แล้วยืนยันที่แท็บ ③ หลังหยุดบอท")
        last = state["status"][-1]["text"] if state["status"] else ""
        if last.startswith("⚠️"):       # ปัญหาที่บอทแจ้งล่าสุดยังต้องเห็น (เช่น เปิด CC ไม่ได้ อ่านรายชื่อในห้องไม่ได้)
            st.warning(last)
        if state["error"]:
            st.error(state["error"])
        rows = state["rows"][-60:][::-1]      # ล่าสุดอยู่บนสุด ไม่ต้องเลื่อนตาม
        with st.container(height=420, border=True):
            if not rows:
                st.caption("ยังไม่มีคำบรรยาย — รอบอทเข้าห้องและเปิด CC (ถ้าอยู่ในห้องรอ ให้ผู้จัดกดอนุญาตให้บอทเข้า)")
            for r in rows:
                name, text = common.md_escape(r["name"]), common.md_escape(r["text"])
                who = f"{r['t']} · {name}" if r.get("t") else name         # เวลาที่พูด · ผู้พูด (รูปแบบเดียวกับแท็บ ③)
                st.markdown(f"**{who}:** {text}" if r["final"] else f":gray[**{who}:** {text}]")

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
