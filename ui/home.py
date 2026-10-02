"""หน้าแรก: รายการการประชุมของฉัน"""

import streamlit as st

import service
from ui import common

FILTERS = ["ทั้งหมด", "ต้องดำเนินการ", "อนุมัติแล้ว"]


def _matches(status: str, flt: str) -> bool:
    if flt == "อนุมัติแล้ว":
        return status == "approved"
    if flt == "ต้องดำเนินการ":
        return status != "approved"
    return True


def render(user: dict):
    st.title("การประชุมของฉัน")
    meetings = service.list_meetings(user)
    if not meetings:
        st.info("ยังไม่มีการประชุม — เริ่มจากสร้างการประชุมใหม่: ใส่ลิงก์ Google Meet และรายชื่อผู้เข้าร่วมพร้อมบทบาท "
                "(ประธาน/เลขา) แล้วค่อยสั่งบอทเข้าห้อง")
        if st.button("➕ สร้างการประชุมแรก", type="primary"):
            common.go("new")
        return

    flt = st.radio("แสดง", FILTERS, horizontal=True, label_visibility="collapsed")
    shown = [m for m in meetings if _matches(m["status"], flt)]
    st.caption(f"{len(shown)} จาก {len(meetings)} รายการ")
    if not shown:
        st.info("ไม่มีการประชุมในกลุ่มนี้")
    for m in shown:
        with st.container(border=True):
            left, mid_col, right = st.columns([5, 3, 1.2], vertical_alignment="center")
            left.markdown(f"**{common.md_escape(m['title'] or '(ไม่ได้ตั้งชื่อ)')}**")
            when = common.thai_date(m["started_at"]) + (f" {m['started_at']:%H:%M} น." if m["status"] != "scheduled" else "")
            left.caption(f"{when} · ข้อความที่บอทจับได้ {m['segment_count']} ช่วง")
            mid_col.markdown(common.STATUS_LABEL.get(m["status"], m["status"]))
            mid_col.caption(common.NEXT_STEP.get(m["status"], ("", ""))[0])
            if right.button("เปิด", key=f"open_{m['meeting_id']}", width="stretch"):
                common.go("meeting", m["meeting_id"])
