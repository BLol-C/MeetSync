"""แท็บ ⑤ Calendar: ตรวจ/แก้งานก่อนอนุมัติ → กดอนุมัติแล้วส่งเข้า Google Calendar ทันที → ยกเลิกอนุมัติ = ลบนัดออกด้วย

งานในตารางนี้คือ "งานที่ได้รับมอบหมาย" ชุดเดียวกับในรายงานและ PDF (ตาราง action_items) จึงแก้ที่นี่แล้ว PDF เปลี่ยนตามทันที
"""

import json
import os

import pandas as pd
import streamlit as st

from bot import botclient
import service
from service import ServiceError
from ui import common, tab_report
from ui.tab_report import NO_ASSIGNEE, _to_date, _to_time

COLS = ["_ev", "งาน / นัดหมาย", "ผู้รับผิดชอบ", "วันที่", "เวลาเริ่ม", "เวลาสิ้นสุด", "สถานะ", "นัดใน Calendar"]
VISIBLE = COLS[1:]
TABLE_MAX_H = 420   # px — ตารางสูงสุดเท่านี้ ที่เกินเลื่อนดูภายในตาราง


def status_text(item: dict, approved: bool) -> str:
    """สถานะส่งเข้า Calendar รายแถว"""
    if item.get("google_calendar_event_id"):
        return "✓ ส่งแล้ว"
    if not item.get("due_date"):
        return "ไม่มีวันที่ — จะไม่ส่ง"
    return "ยังไม่ส่ง" if approved else "จะส่งตอนอนุมัติ"


def items_df(items: list[dict], approved: bool) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "_ev": json.dumps(it.get("evidence") or [], ensure_ascii=False),
            "งาน / นัดหมาย": it["description"], "ผู้รับผิดชอบ": it.get("assignee") or NO_ASSIGNEE,
            "วันที่": _to_date(it.get("due_date")), "เวลาเริ่ม": _to_time(it.get("due_time")),
            "เวลาสิ้นสุด": _to_time(it.get("due_time_end")),
            "สถานะ": status_text(it, approved), "นัดใน Calendar": it.get("google_calendar_link") or None,
        } for it in items],
        columns=COLS,
    )


def _task_key(items: list[dict]) -> list[tuple]:
    return [((it["description"] or "").strip(), it.get("assignee") or None, it.get("due_date") or None,
             it.get("due_time") or None, it.get("due_time_end") or None) for it in items if (it["description"] or "").strip()]


def tasks_unsaved(saved: list[dict], edited: list[dict]) -> bool:
    """งานในตารางที่แก้ค้างอยู่ต่างจากที่บันทึกไว้หรือไม่"""
    return _task_key(saved) != _task_key(edited)


def _invites_default() -> bool:
    return os.environ.get("CALENDAR_SEND_INVITES") == "1"


def _show_sync_result(mid: int):
    last = st.session_state.pop(f"sync_result_{mid}", None)
    if not last:
        return
    if last["sent"]:
        st.success(f"✅ ส่งเข้า Google Calendar เรียบร้อยแล้ว {last['sent']} รายการ")
    for r in last["failed"]:
        st.error(f"ส่งไม่สำเร็จ: {common.md_escape(r['description'])} — {r['error']}")
    if not last["sent"] and not last["failed"]:
        st.info("ไม่มีงานให้ส่ง (ไม่มีงานที่ระบุวันที่ หรือส่งครบแล้ว)")


def _store_results(mid: int, results: list[dict]):
    st.session_state[f"sync_result_{mid}"] = {
        "sent": sum(1 for r in results if r["ok"]), "failed": [r for r in results if not r["ok"]]}


@st.dialog("ยืนยันการอนุมัติรายงานและส่งเข้า Calendar")
def _approve_dialog(user: dict, mid: int, warnings: list[dict], dated: int, connected: bool, invites: bool):
    if warnings:
        st.warning("ยังมีรายการที่ควรตรวจก่อนอนุมัติ:")
        for w in warnings:
            st.markdown(f"- {w['message']}")
        ok = st.checkbox("ฉันตรวจแล้ว และต้องการอนุมัติต่อไป")
    else:
        st.success("ไม่พบรายการที่ต้องตรวจ")
        ok = True
    if not dated:
        st.caption("ไม่มีงานที่ระบุวันที่ จึงไม่มีอะไรส่งเข้า Calendar")
    elif connected:
        st.caption(f"เมื่ออนุมัติ ระบบจะส่ง {dated} งานที่มีวันที่เข้า Google Calendar ของคุณทันที"
                   + (" และส่งอีเมลเชิญผู้รับผิดชอบที่มีอีเมล" if invites else " (ไม่ส่งอีเมลเชิญ)"))
    else:
        st.warning("ยังไม่ได้เชื่อมต่อ Google Calendar — จะอนุมัติรายงานแต่ยังไม่ส่งนัด (เชื่อมต่อแล้วกดส่งภายหลังได้)")
    st.caption("อนุมัติแล้วรายงานและงานจะถูกล็อก ถ้าต้องแก้ ยกเลิกการอนุมัติได้ (นัดที่ส่งไปแล้วจะถูกลบออกจาก Calendar)")
    if st.button("✅ อนุมัติและส่ง", type="primary", disabled=not ok):
        try:
            results = service.approve_and_send(user, mid, confirm_warnings=bool(warnings), send_invites=invites)
        except ServiceError as e:
            st.error(str(e))
            return
        _store_results(mid, results)
        tab_report._bump(mid)
        common.flash("success", "อนุมัติรายงานแล้ว — ดาวน์โหลด PDF ได้")
        st.rerun()


def render(user: dict, detail: dict):
    meeting = detail["meeting"]
    mid = meeting["meeting_id"]
    status = meeting["status"]
    st.subheader("ตรวจงานและส่งเข้า Google Calendar")
    view = service.get_report_view(user, mid)
    report = view["report"]
    if not report or status not in ("draft", "approved"):
        st.info("ใช้ได้เมื่อมีรายงานฉบับร่างแล้ว — ให้ AI ร่างรายงานที่แท็บ ④ ก่อน")
        return
    approved = report["approved"]
    content = report["content"]
    connected = service.calendar_connected(user)
    items = content["action_items"]

    if not connected:
        st.caption("ยังไม่ได้เชื่อมต่อ Google Calendar ของบัญชีนี้ (ขอสิทธิ์สร้างนัดหมายอย่างเดียว แยกจากการล็อกอิน) — อนุมัติรายงานได้ แต่จะยังไม่ส่งนัด")
        st.link_button("เชื่อมต่อ Google Calendar", botclient.calendar_connect_url(user, f"{botclient.UI_URL}/?m={mid}"))
    _show_sync_result(mid)
    if not approved:
        for w in content["warnings"]:
            if w["path"].startswith("action_items"):
                st.warning(w["message"])

    names = [NO_ASSIGNEE] + [p["display_name"] for p in view["people"]]
    edited = st.data_editor(
        items_df(items, approved), key=f"cal_{mid}_{tab_report._rev(mid)}", hide_index=True, width="stretch",
        num_rows="fixed" if approved else "dynamic", height=common.table_height(len(items), max_px=TABLE_MAX_H, spare_rows=0 if approved else 2),
        disabled=True if approved else ["สถานะ", "นัดใน Calendar"], column_order=VISIBLE,
        column_config={
            # ความกว้างเป็นพิกเซล (รวมราว 900 ให้พอดีหน้าจอที่มีแถบด้านข้าง) ให้ทุกคอลัมน์อยู่ในหน้าจอ — เกินกว่านี้ตารางเลื่อนซ้าย-ขวาในตัวเอง
            "งาน / นัดหมาย": st.column_config.TextColumn("งาน / นัดหมาย", width=250),
            "ผู้รับผิดชอบ": st.column_config.SelectboxColumn("ผู้รับผิดชอบ", options=names, default=NO_ASSIGNEE, width=120),
            "วันที่": st.column_config.DateColumn("วันที่", format="YYYY-MM-DD", width=100),
            "เวลาเริ่ม": st.column_config.TimeColumn("เวลาเริ่ม", format="HH:mm", step=60, width=80),
            "เวลาสิ้นสุด": st.column_config.TimeColumn("เวลาสิ้นสุด", format="HH:mm", step=60, width=90),
            "สถานะ": st.column_config.TextColumn("สถานะ", width=150),
            "นัดใน Calendar": st.column_config.LinkColumn("นัดใน Calendar", display_text="เปิดนัด", width=100),
        },
    )
    pending = [it for it in items if it.get("due_date") and not it.get("google_calendar_event_id")]
    # อนุมัติแล้ว และส่งครบแล้ว = ตัวเลือกนี้ไม่มีผลอีก จึงล็อกไว้ (ยังใช้ได้เมื่อมีงานค้างให้กดส่งซ้ำ)
    invites = st.checkbox("ส่งอีเมลเชิญผู้รับผิดชอบที่มีอีเมลในรายชื่อด้วย", value=_invites_default(), key=f"cal_invites_{mid}",
                          disabled=approved and not pending)

    edited_items = tab_report.actions_from_df(edited)

    def collect() -> dict:
        return {**content, "action_items": edited_items}

    report_unsaved = (not approved) and bool(st.session_state.get(f"report_unsaved_{mid}"))
    if not approved:
        if report_unsaved:
            st.warning("แท็บ ④ รายงาน มีการแก้ไขที่ยังไม่ได้บันทึก — กลับไปกด 💾 บันทึกร่าง ก่อนอนุมัติ (ปุ่มอนุมัติปิดไว้จนกว่าจะบันทึก)")
        if tasks_unsaved(items, edited_items):
            st.warning("มีการแก้ไขงานที่ยังไม่ได้บันทึก — กด 💾 บันทึกงาน")

    b1, b2, _ = st.columns([1.3, 3, 2])
    if not approved:
        if b1.button("💾 บันทึกงาน", key=f"save_cal_{mid}"):
            try:
                service.save_report_draft(user, mid, collect())
            except ServiceError as e:
                st.error(str(e))
            else:
                tab_report._bump(mid)
                common.flash("success", "บันทึกงานแล้ว (แก้ในรายงาน/PDF ตามด้วย)")
                st.rerun()
        if b2.button("✅ อนุมัติรายงานและส่งเข้า Calendar…", key=f"approve_{mid}", type="primary", disabled=report_unsaved):
            try:   # บันทึกงานที่แก้ค้างไว้ก่อนเสมอ แล้วเอาคำเตือนล่าสุดมาให้ยืนยัน
                cleaned = service.save_report_draft(user, mid, collect())
            except ServiceError as e:
                st.error(str(e))
            else:
                dated = sum(1 for it in cleaned["action_items"] if it.get("due_date"))
                _approve_dialog(user, mid, cleaned["warnings"] + view["header_warnings"], dated, connected, invites)
        return

    # อนุมัติแล้ว: ปุ่มเรียงแถวเดียวชิดซ้าย ขนาดเท่ากัน — ดาวน์โหลด PDF ฉบับเต็ม (ไม่มีลายน้ำ) | ส่งงานที่ค้าง (ถ้ามี) | ยกเลิกการอนุมัติ
    d, r, y, _ = st.columns([1.5, 1.7, 1.7, 1.1])
    try:
        pdf, name = service.build_pdf(user, mid)
        d.download_button("⬇ ดาวน์โหลด PDF ฉบับเต็ม", data=pdf, file_name=name, mime="application/pdf",
                          key=f"pdf_{mid}", type="primary", width="stretch")
    except ServiceError as e:
        d.caption(str(e))
    if r.button("✎ ยกเลิกการอนุมัติเพื่อแก้", key=f"reopen_cal_{mid}", width="stretch"):
        tab_report.reopen_dialog(user, mid)
    if pending and y.button(f"📅 ส่งงานที่ยังไม่ส่ง ({len(pending)})", key=f"sync_{mid}", disabled=not connected, width="stretch"):
        with st.spinner("กำลังส่งเข้า Google Calendar…"):
            _store_results(mid, service.sync_calendar(user, mid, invites))
        tab_report._bump(mid)
        st.rerun()
