"""หน้าแรก: รายการการประชุมของฉัน"""

import streamlit as st

import service
from service import ServiceError
from ui import common

FILTERS = ["ทั้งหมด", "ต้องดำเนินการ", "อนุมัติแล้ว"]


def _matches(status: str, flt: str) -> bool:
    if flt == "อนุมัติแล้ว":
        return status == "approved"
    if flt == "ต้องดำเนินการ":
        return status != "approved"
    return True


@st.dialog("ลบการประชุม")
def delete_dialog(user: dict, m: dict):
    mid = m["meeting_id"]
    st.markdown(f"ลบ **{common.md_escape(m['title'] or '(ไม่ได้ตั้งชื่อ)')}** — ผู้เข้าร่วม transcript รายงาน และงานทั้งหมดจะถูกลบ กู้คืนไม่ได้")
    if m["status"] == "approved":
        st.warning("รายงานนี้อนุมัติแล้ว — นัดที่ส่งเข้า Google Calendar จะถูกลบออกด้วย")
    try:
        following = service.following_meetings(user, mid)
    except ServiceError as e:
        st.error(str(e))
        return
    if following:
        st.warning("การประชุมนี้เป็น “ประชุมครั้งก่อน” ของ: " + ", ".join(common.md_escape(f["title"]) for f in following)
                   + " — ถ้าลบ ประชุมเหล่านั้นจะไม่มีประชุมครั้งก่อน และเสียวาระต่อเนื่องที่ติดมาจากครั้งนี้")
    failed = st.session_state.get(f"delete_failed_{mid}")
    if failed:
        st.error("ลบนัดออกจาก Google Calendar ไม่สำเร็จ จึงยังไม่ลบการประชุม:")
        for f in failed:
            st.markdown(f"- {common.md_escape(f['description'])} — {f['error']}")
    force = st.checkbox("ลบการประชุมต่อไป แม้นัดข้างต้นจะค้างใน Calendar (ฉันจะลบเอง)", key=f"delete_force_{mid}") if failed else False
    sure = st.checkbox("ยืนยันว่าต้องการลบ", key=f"delete_ok_{mid}")
    if st.button("ลบการประชุม", type="primary", disabled=not sure, key=f"delete_go_{mid}"):
        try:
            out = service.delete_meeting(user, mid, force=force)
        except ServiceError as e:
            if e.kind == "calendar_failed":
                st.session_state[f"delete_failed_{mid}"] = e.extra["failed"]
                st.rerun(scope="fragment")
            st.error(str(e))
            return
        st.session_state.pop(f"delete_failed_{mid}", None)
        common.flash("success", "ลบการประชุมแล้ว" + (f" (ลบนัดออกจาก Google Calendar {out['deleted']} รายการ)" if out["deleted"] else ""))
        if out["failed"]:
            common.flash("warning", "นัดที่ยังค้างอยู่ใน Google Calendar (ต้องลบเอง): " + ", ".join(f["description"] for f in out["failed"]))
        st.rerun()


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
            left, mid_col, right, trash = st.columns([5, 3, 1.2, 0.7], vertical_alignment="center")
            left.markdown(f"**{common.md_escape(m['title'] or '(ไม่ได้ตั้งชื่อ)')}**")
            when = common.thai_date(m["started_at"]) + (f" {m['started_at']:%H:%M} น." if m["status"] != "scheduled" else "")
            left.caption(when)
            mid_col.markdown(common.STATUS_LABEL.get(m["status"], m["status"]))
            mid_col.caption(common.NEXT_STEP.get(m["status"], ("", ""))[0])
            if right.button("เปิด", key=f"open_{m['meeting_id']}", width="stretch"):
                common.go("meeting", m["meeting_id"])
            if trash.button("🗑", key=f"del_{m['meeting_id']}", width="stretch", help="ลบการประชุมนี้"):
                delete_dialog(user, m)
