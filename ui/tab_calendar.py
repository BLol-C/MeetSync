"""แท็บ ⑤ Calendar: งานที่จะส่งเข้า Google Calendar — แก้/เพิ่ม/ลบได้หลังอนุมัติรายงาน (แยกจากงานในรายงานที่ล็อกแล้ว)"""

import os

import pandas as pd
import streamlit as st

from bot import botclient
import service
from service import ServiceError
from ui import common
from ui.common import clean_str
from ui.tab_report import NO_ASSIGNEE, _date_str, _time_str, _to_date, _to_time

COLS = ["calendar_task_id", "งาน / นัดหมาย", "ผู้รับผิดชอบ", "วันที่", "เวลาเริ่ม", "เวลาสิ้นสุด", "สถานะ", "นัดใน Calendar"]
VISIBLE = COLS[1:]
STATUS_LABEL = {"none": "ยังไม่ส่ง", "synced": "✓ ส่งแล้ว", "changed": "⚠ แก้หลังส่ง"}
TABLE_MAX_H = 420   # px — ตารางสูงสุดเท่านี้ ที่เกินเลื่อนดูภายในตาราง


def _rev(mid: int) -> int:
    return st.session_state.get(f"rev_calendar_{mid}", 0)


def status_text(task: dict) -> str:
    """สถานะรายแถว — งานที่ไม่มีวันที่ส่งไม่ได้ จึงบอกตรง ๆ แทนที่จะขึ้น "ยังไม่ส่ง" เฉย ๆ"""
    if task["sync_status"] == "none" and not task["due_date"]:
        return "ไม่มีวันที่ — ส่งไม่ได้"
    return STATUS_LABEL[task["sync_status"]]


def tasks_df(tasks: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "calendar_task_id": t["calendar_task_id"], "งาน / นัดหมาย": t["description"],
            "ผู้รับผิดชอบ": t["assignee"] or NO_ASSIGNEE, "วันที่": _to_date(t["due_date"]),
            "เวลาเริ่ม": _to_time(t["due_time"]), "เวลาสิ้นสุด": _to_time(t["due_time_end"]),
            "สถานะ": status_text(t), "นัดใน Calendar": t.get("google_calendar_link") or None,
        } for t in tasks],
        columns=COLS,
    )


def rows_from_df(df: pd.DataFrame) -> list[dict]:
    """ตารางที่แก้ในหน้าเว็บ -> แถวสำหรับ service.apply_calendar_table (แถวที่ไม่มีชื่องานและไม่มี id ถูกข้ามที่ชั้น service)"""
    rows = []
    for rec in df.to_dict("records"):
        tid = rec.get("calendar_task_id")
        assignee = clean_str(rec.get("ผู้รับผิดชอบ"))
        rows.append({
            "calendar_task_id": None if tid is None or tid != tid else int(tid),
            "description": clean_str(rec.get("งาน / นัดหมาย")),
            "assignee": None if assignee in ("", NO_ASSIGNEE) else assignee,
            "due_date": _date_str(rec.get("วันที่")), "due_time": _time_str(rec.get("เวลาเริ่ม")),
            "due_time_end": _time_str(rec.get("เวลาสิ้นสุด")),
        })
    return rows


def _save(user: dict, mid: int, df: pd.DataFrame) -> dict:
    result = service.apply_calendar_table(user, mid, rows_from_df(df))
    st.session_state[f"rev_calendar_{mid}"] = _rev(mid) + 1
    parts = [f"{label} {result[k]} งาน" for k, label in (("added", "เพิ่ม"), ("updated", "แก้"), ("deleted", "ลบ")) if result[k]]
    if parts:
        common.flash("success", "บันทึกงานแล้ว: " + ", ".join(parts))
    for err in result["errors"]:
        common.flash("error", err)
    return result


def render(user: dict, detail: dict):
    meeting = detail["meeting"]
    mid = meeting["meeting_id"]
    st.subheader("ส่งงานเข้า Google Calendar")
    if meeting["status"] != "approved":
        st.info("ใช้ได้หลังอนุมัติรายงานแล้ว — อนุมัติที่แท็บ ④ รายงานก่อน")
        return
    try:
        view = service.calendar_view(user, mid)
    except ServiceError as e:
        st.error(str(e))
        return
    tasks = view["tasks"]
    connected = service.calendar_connected(user)
    if not connected:
        st.caption("ยังไม่ได้เชื่อมต่อ Google Calendar ของบัญชีนี้ (ขอสิทธิ์สร้างนัดหมายอย่างเดียว แยกจากการล็อกอิน) — แก้งานได้ แต่ส่งเข้า Calendar ไม่ได้")
        st.link_button("เชื่อมต่อ Google Calendar", botclient.calendar_connect_url(user, f"{botclient.UI_URL}/?m={mid}"))

    last = st.session_state.pop(f"sync_result_{mid}", None)
    if last:
        if last["created"] or last["updated"]:
            parts = [f"{label} {last[k]} รายการ" for k, label in (("created", "สร้างนัดใหม่"), ("updated", "อัปเดตนัดเดิม")) if last[k]]
            st.success("✅ ส่งเข้า Google Calendar แล้ว: " + ", ".join(parts))
        for r in last["failed"]:
            st.error(f"ส่งไม่สำเร็จ: {common.md_escape(r['description'])} — {r['error']}")
        if not (last["created"] or last["updated"] or last["failed"]):
            st.info("ไม่มีงานให้ส่ง (ส่งครบแล้ว หรือยังไม่มีงานที่ระบุวันที่)")

    names = [NO_ASSIGNEE] + [p["display_name"] for p in view["people"]]
    edited = st.data_editor(
        tasks_df(tasks), key=f"caltasks_{mid}_{_rev(mid)}", hide_index=True, width="stretch", num_rows="dynamic",
        height=common.table_height(len(tasks), max_px=TABLE_MAX_H, spare_rows=2), disabled=["สถานะ", "นัดใน Calendar"],
        column_order=VISIBLE,
        column_config={
            "งาน / นัดหมาย": st.column_config.TextColumn("งาน / นัดหมาย", width="large"),
            "ผู้รับผิดชอบ": st.column_config.SelectboxColumn("ผู้รับผิดชอบ", options=names, default=NO_ASSIGNEE, width="medium"),
            "วันที่": st.column_config.DateColumn("วันที่", format="YYYY-MM-DD", width="small"),
            "เวลาเริ่ม": st.column_config.TimeColumn("เวลาเริ่ม", format="HH:mm", step=60, width="small"),
            "เวลาสิ้นสุด": st.column_config.TimeColumn("เวลาสิ้นสุด", format="HH:mm", step=60, width="small"),
            "สถานะ": st.column_config.TextColumn("สถานะ", width="small"),
            "นัดใน Calendar": st.column_config.LinkColumn("นัดใน Calendar", display_text="เปิดนัด", width="small"),
        },
    )

    missing = view["report_items_missing"]
    b1, b2, _ = st.columns([1.3, 2.2, 3])
    if b1.button("💾 บันทึกงาน", key=f"save_cal_{mid}"):
        _save(user, mid, edited)
        st.rerun()
    if missing and b2.button(f"⬇ นำเข้างานจากรายงานที่ยังไม่อยู่ในรายการ ({len(missing)})", key=f"import_cal_{mid}"):
        added = service.import_report_tasks(user, mid)
        st.session_state[f"rev_calendar_{mid}"] = _rev(mid) + 1
        common.flash("success", f"นำเข้างานจากรายงาน {added} งาน")
        st.rerun()

    st.divider()
    invites = st.checkbox("ส่งอีเมลเชิญผู้รับผิดชอบที่มีอีเมลในรายชื่อด้วย", value=os.environ.get("CALENDAR_SEND_INVITES") == "1",
                          key=f"cal_invites_{mid}")
    pending = [t for t in tasks if t["due_date"] and t["sync_status"] != "synced"]
    label = f"📅 ส่งเข้า Calendar ({len(pending)} งาน)" if pending else "📅 ส่งเข้า Calendar"
    if st.button(label, key=f"sync_{mid}", type="primary", disabled=not connected or not (pending or len(edited) != len(tasks))):
        try:   # บันทึกที่แก้ค้างไว้ก่อนเสมอ แล้วส่งจากข้อมูลที่บันทึกแล้ว
            _save(user, mid, edited)
            with st.spinner("กำลังส่งเข้า Google Calendar…"):
                results = service.sync_calendar_tasks(user, mid, invites)
        except ServiceError as e:
            st.error(str(e))
        else:
            ok = [r for r in results if r["ok"]]
            st.session_state[f"sync_result_{mid}"] = {
                "created": sum(1 for r in ok if r["action"] == "created"), "updated": sum(1 for r in ok if r["action"] == "updated"),
                "failed": [r for r in results if not r["ok"]],
            }
            st.session_state[f"rev_calendar_{mid}"] = _rev(mid) + 1
            st.rerun()
