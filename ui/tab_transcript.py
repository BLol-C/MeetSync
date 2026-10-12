"""แท็บ ③ Transcript: รวมชื่อที่ซ้ำ, แก้ข้อความ/ผู้พูด/ลบ, ยืนยัน transcript (ปลดล็อกให้ AI ร่างรายงาน)"""

import pandas as pd
import streamlit as st

import service
from service import ServiceError
from ui import common
from ui.common import clean_str

COLS = ["segment_id", "เวลา", "ผู้พูด", "ข้อความ", "ลบ", "แก้แล้ว"]


CONVERSATION_MAX_H = 420   # px — กล่องบทสนทนาสูงสุดเท่านี้ ที่เกินเลื่อนในกล่อง


def _rev(mid: int) -> int:
    return st.session_state.get(f"rev_segs_{mid}", 0)


def _segments_df(segments: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "segment_id": s["segment_id"], "เวลา": f"{s['spoken_at']:%H:%M:%S}", "ผู้พูด": s["display_name"],
            "ข้อความ": s["text"], "ลบ": s["deleted"], "แก้แล้ว": "✎" if s["original_text"] else "",
        } for s in segments],
        columns=COLS,
    )


def segment_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for rec in df.to_dict("records"):
        sid = rec.get("segment_id")
        if sid is None or sid != sid:
            continue
        rows.append({"segment_id": int(sid), "display_name": clean_str(rec.get("ผู้พูด")),
                     "text": clean_str(rec.get("ข้อความ")), "deleted": bool(rec.get("ลบ"))})
    return rows


def evidence_text(p: dict) -> str:
    """หลักฐานว่าคนนี้เข้าร่วมประชุมหรือไม่ — จากสิ่งที่บอทเห็นจริง (เห็นชื่อในห้อง / เคยพูด) ไม่ใช่การเดา"""
    parts = []
    if p.get("seen_in_room"):
        parts.append("เห็นในห้อง")
    if p.get("segment_count"):
        parts.append(f"พูด {p['segment_count']} ช่วง")
    if parts:
        return " · ".join(parts)
    return "ผู้กรอกตั้งไว้ (ไม่พบหลักฐานจากบอท)" if p["attendance"] != "invited" else "ไม่พบหลักฐาน (ไม่เห็นในห้อง ไม่เคยพูด)"


def evidence_df(people: list[dict]) -> pd.DataFrame:
    rows = []
    for p in people:
        if p["source"] == "excluded":
            continue
        outsider = p["source"] == "meet"
        rows.append({
            "ชื่อ": p["display_name"] + (" (นอกรายชื่อ)" if outsider else ""),
            "บทบาท": "—" if outsider else common.ROLE_LABEL.get(p["role"], p["role"]),
            "สถานะ": "รอยืนยัน" if outsider else common.ATT_LABEL.get(p["attendance"], p["attendance"]),
            "หลักฐานจากบอท": evidence_text(p),
        })
    return pd.DataFrame(rows, columns=["ชื่อ", "บทบาท", "สถานะ", "หลักฐานจากบอท"])


def _confirm_section(user: dict, people: list[dict], editable: bool) -> int:
    """หัวข้อเดียว "ยืนยันผู้เข้าร่วม" — ต้องตอบให้ครบก่อนยืนยัน transcript คืนจำนวนที่ยังไม่ได้ตอบ

    1) คนที่ลงทะเบียนไว้แต่ยังไม่ยืนยัน (บอทไม่เห็นชื่อตรงกับ "ชื่อภาษาอังกฤษ"): เลือกว่าตรงกับชื่อไหนที่เห็นใน Meet (กรณีสะกดผิด)
       หรือยืนยันว่ามา (เข้าห้องแต่ไม่พูด) / ไม่มา (พร้อมสาเหตุ)
    2) ชื่อที่ Meet แสดงแต่ไม่อยู่ในรายชื่อ: เลือกว่าเป็นคนไหนในรายชื่อ / ผู้มาประชุมทั่วไป / ไม่นับในรายงาน"""
    pending = [p for p in people if p["source"] == "registered" and p["attendance"] == "invited"]
    outsiders = [p for p in people if p["source"] == "meet"]
    if not pending and not outsiders:
        return 0
    registered = [p for p in people if p["source"] == "registered"]

    def seen_label(m):
        return (f"{m['display_name']} — พูด {m['segment_count']} ช่วง" if m["segment_count"] else f"{m['display_name']} — อยู่ในห้อง")

    st.subheader("ยืนยันผู้เข้าร่วม")
    st.dataframe(evidence_df(people), hide_index=True, width="stretch", height=common.table_height(len(people), max_px=300))
    done = lambda msg, mid: (common.flash("success", msg), st.session_state.__setitem__(f"rev_segs_{mid}", _rev(mid) + 1), st.rerun())  # noqa: E731

    for p in pending:
        labels = {f"meet:{m['speaker_id']}": seen_label(m) for m in outsiders}
        labels["present"] = "มาประชุม (เข้าห้องแต่ไม่พูด / ไม่พบชื่อใน Meet)"
        labels["absent"] = "ไม่ได้เข้าประชุม (ไม่มา)"
        with st.container(border=True):
            a, b, c = st.columns([3, 4, 1.2], vertical_alignment="center")
            a.markdown(f"**{common.md_escape(p['display_name'])}**  \n{common.ROLE_LABEL.get(p['role'], p['role'])}")
            choice = b.selectbox("ตรงกับ", options=list(labels), format_func=labels.get, index=None,
                                 placeholder="เลือกชื่อใน Meet ที่ตรงกัน หรือยืนยันมา/ไม่มา…", key=f"unc_{p['speaker_id']}",
                                 label_visibility="collapsed", disabled=not editable)
            reason = ""
            if choice == "absent":
                reason = st.text_input("สาเหตุที่ไม่มา (ถ้ามี)", value=p.get("absence_reason") or "", key=f"unc_reason_{p['speaker_id']}")
            if c.button("ยืนยัน", key=f"unc_go_{p['speaker_id']}", disabled=not editable or choice is None, width="stretch"):
                try:
                    if choice in ("present", "absent"):
                        service.set_person_attendance(user, p["speaker_id"], choice, reason if choice == "absent" else None)
                        msg = "ตั้งเป็น " + ("เข้าร่วม" if choice == "present" else "ไม่มา") + f": {p['display_name']}"
                    else:
                        moved = service.merge_people(user, int(choice.split(":")[1]), p["speaker_id"])
                        msg = f"จับคู่แล้ว: {p['display_name']} เข้าร่วม" + (f" (ย้าย {moved} ช่วง)" if moved else "")
                except ServiceError as e:
                    st.error(str(e))
                else:
                    done(msg, p["meeting_id"])

    for m in outsiders:
        labels = {f"person:{r['speaker_id']}": f"คือ {r['display_name']}" for r in registered}
        labels["guest"] = "ผู้มาประชุมทั่วไป (ไม่อยู่ในรายชื่อ)"
        labels["exclude"] = "ไม่นับในรายงาน"
        with st.container(border=True):
            a, b, c = st.columns([3, 4, 1.2], vertical_alignment="center")
            a.markdown(f"**{common.md_escape(m['display_name'])}**  \nชื่อใน Meet ที่ไม่อยู่ในรายชื่อ · "
                       + ("พูด " + str(m["segment_count"]) + " ช่วง" if m["segment_count"] else "อยู่ในห้อง"))
            choice = b.selectbox("เป็นใคร", options=list(labels), format_func=labels.get, index=None,
                                 placeholder="เลือกว่าชื่อนี้คือใคร…", key=f"out_{m['speaker_id']}",
                                 label_visibility="collapsed", disabled=not editable)
            if c.button("ยืนยัน", key=f"out_go_{m['speaker_id']}", disabled=not editable or choice is None, width="stretch"):
                try:
                    if choice == "guest":
                        service.confirm_guest(user, m["speaker_id"])
                        msg = f"ยืนยันเป็นผู้มาประชุมทั่วไป: {m['display_name']}"
                    elif choice == "exclude":
                        how = service.exclude_person(user, m["speaker_id"])
                        msg = f"ไม่นับในรายงาน: {m['display_name']}" + (" (ซ่อนไว้ เพราะมีข้อความที่ยังอ้างอิงอยู่)" if how == "hidden" else "")
                    else:
                        moved = service.merge_people(user, m["speaker_id"], int(choice.split(":")[1]))
                        msg = f"จับคู่แล้ว: {m['display_name']} = {labels[choice][4:]}" + (f" (ย้าย {moved} ช่วง)" if moved else "")
                except ServiceError as e:
                    st.error(str(e))
                else:
                    done(msg, m["meeting_id"])
    return len(pending) + len(outsiders)


def render(user: dict, detail: dict):
    meeting = detail["meeting"]
    mid = meeting["meeting_id"]
    status = meeting["status"]
    people = detail["people"]
    bot = detail["bot"]

    if status == "scheduled":
        st.info("ยังไม่มีข้อความ — เริ่มบอทที่แท็บ ② ก่อน")
        return
    if bot["running_here"]:
        st.warning("บอทกำลังบันทึกการประชุมนี้อยู่ — หยุดบอทที่แท็บ ② ก่อนจึงจะตรวจทานและยืนยันได้")

    segments = service.list_segments(user, mid)
    editable = status == "transcript_review" and not bot["running_here"]
    if status in ("transcript_verified", "draft"):
        st.success("✓ ยืนยัน transcript แล้ว — ถ้าจะแก้ ต้องกด “กลับไปแก้ transcript” ด้านล่างก่อน")
    elif status == "approved":
        st.success("🔒 รายงานอนุมัติแล้ว transcript ถูกล็อก")

    people_in_meeting = [{**p, "meeting_id": mid} for p in people]
    unresolved = _confirm_section(user, people_in_meeting, editable and status != "approved")

    if not segments:
        st.subheader("ข้อความที่บอทจับได้")
        st.info("ยังไม่มีข้อความที่บอทจับได้ — ถ้าประชุมจบแล้วแต่ไม่มีข้อความ ให้ตรวจที่แท็บ ② ว่าบอทเข้าห้องและเปิดคำบรรยาย (CC) ได้หรือไม่")
        return

    # มุมมองอ่านง่าย: รวมประโยคต่อเนื่องของคนเดียวกันเป็นช่วงพูด (ในฐานข้อมูลยังเป็น 1 ประโยค = 1 แถว)
    turns = service.group_turns(segments)
    st.subheader("บทสนทนา")
    st.caption(f"ประโยคต่อเนื่องของคนเดียวกัน (ห่างกันไม่เกิน {service.TURN_GAP_S} วินาที) รวมเป็นช่วงพูดเดียวเพื่อให้อ่านง่าย · "
               "✎ = มีประโยคที่ถูกแก้ · แก้ไขได้ที่ตารางรายประโยคด้านล่าง")
    # กล่องสูงตามเนื้อหา (ประมาณ) แต่ไม่เกิน CONVERSATION_MAX_H — ยาวกว่านั้นเลื่อนอ่านภายในกล่อง
    lines = sum(2 + len(t["text"]) // 80 for t in turns)
    with st.container(height=min(CONVERSATION_MAX_H, max(120, 70 + lines * 26)), border=True):
        st.markdown("\n\n".join(
            f"**{service.format_turn_time(t)} · {common.md_escape(t['display_name'])}**{' ✎' if t['edited'] else ''}  \n"
            f"{common.md_escape(t['text'])}" for t in turns) or "_ไม่มีข้อความ (ลบทั้งหมด)_")

    speaker_options = [p["display_name"] for p in people]
    df = _segments_df(segments)
    # expander ยังวาดเนื้อหาทุกครั้งแม้พับอยู่ จึงไม่ทำให้ค่าที่แก้ค้างในตารางหายตอนสลับแท็บ
    with st.expander("✎ แก้ไข / ลบ รายประโยค" if editable else "ดูรายประโยค", expanded=False):
        if editable:
            st.caption("แก้ข้อความที่ถอดผิด / เปลี่ยนผู้พูด / ติ๊ก “ลบ” ช่วงที่ไม่เกี่ยวข้อง แล้วกดบันทึก — ข้อความต้นฉบับยังเก็บไว้ "
                       "(ช่วงที่ลบยังกู้คืนได้โดยเอาติ๊กออก) ค้นหาได้ด้วยไอคอนแว่นขยายมุมขวาบนของตาราง "
                       "ผู้พูดเลือกได้เฉพาะคนในรายชื่อ — ถ้าเป็นคนใหม่ให้เพิ่มที่แท็บ ① ก่อน")
        elif status == "transcript_review":
            st.caption("ดูอย่างเดียวขณะที่บอทยังบันทึกอยู่")
        edited = st.data_editor(
            df, key=f"segs_{mid}_{_rev(mid)}", hide_index=True, width="stretch", height=common.table_height(len(df), max_px=520),
            num_rows="fixed", disabled=True if not editable else ["เวลา", "แก้แล้ว"], column_order=COLS[1:],
            column_config={
                "เวลา": st.column_config.TextColumn("เวลา", width="small"),
                "ผู้พูด": st.column_config.SelectboxColumn("ผู้พูด", options=speaker_options, required=True, width="medium"),
                "ข้อความ": st.column_config.TextColumn("ข้อความ", width="large"),
                "ลบ": st.column_config.CheckboxColumn("ลบ", width="small"),
                "แก้แล้ว": st.column_config.TextColumn("แก้แล้ว", width="small", help="✎ = มีการแก้ไข (เก็บต้นฉบับไว้)"),
            },
        )

    def save_edits() -> bool:
        try:
            result = service.apply_segments_table(user, mid, segment_rows(edited))
        except ServiceError as e:
            st.error(str(e))
            return False
        for err in result["errors"]:
            common.flash("error", err)
        if result["changed"]:
            common.flash("success", f"บันทึกการแก้ไขแล้ว {result['changed']} ช่วง")
        st.session_state[f"rev_segs_{mid}"] = _rev(mid) + 1
        return not result["errors"]

    left, mid_col, right = st.columns([1.2, 2.2, 1.6])
    if editable:
        if left.button("💾 บันทึกการแก้ไข", key=f"save_segs_{mid}"):
            save_edits()
            st.rerun()
        if mid_col.button("✓ ยืนยัน transcript (ปลดล็อกให้ AI ร่างรายงาน)", key=f"verify_{mid}", type="primary",
                          disabled=bool(unresolved), help=f"ยังยืนยันผู้เข้าร่วมไม่ครบ ({unresolved} รายการ)" if unresolved else None):
            if save_edits():   # ยืนยันสิ่งที่เห็นอยู่ในตารางตอนนี้ — บันทึกที่แก้ค้างไว้ก่อนเสมอ ไม่ทิ้งการแก้ไขโดยไม่รู้ตัว
                try:
                    out = service.verify_transcript(user, mid, bot_running=bot["running_here"])
                except ServiceError as e:
                    st.error(str(e))
                else:
                    common.flash("success", "ยืนยัน transcript แล้ว — ไปแท็บ ④ รายงาน เพื่อให้ AI ร่างรายงาน")
                    st.rerun()
            else:
                st.rerun()
    elif status in ("transcript_verified", "draft"):
        if left.button("↩ กลับไปแก้ transcript", key=f"reopen_{mid}"):
            _reopen_dialog(user, mid, has_report=status == "draft")
    right.download_button("⬇ ดาวน์โหลด .txt", data=service.transcript_text(user, mid),
                          file_name=f"transcript-{mid}.txt", mime="text/plain", key=f"dl_txt_{mid}")


@st.dialog("กลับไปแก้ transcript")
def _reopen_dialog(user: dict, mid: int, has_report: bool):
    if has_report:
        st.warning("รายงานฉบับร่างที่มีอยู่จะล้าสมัย (สร้างจาก transcript เดิม) — หลังแก้เสร็จต้องยืนยัน transcript แล้วให้ AI ร่างรายงานใหม่")
    else:
        st.write("กลับไปที่ขั้นตรวจทานเพื่อแก้ transcript ต่อ แล้วค่อยยืนยันอีกครั้ง")
    if st.button("กลับไปแก้", type="primary"):
        try:
            service.reopen_transcript(user, mid)
        except ServiceError as e:
            st.error(str(e))
            return
        common.flash("info", "กลับสู่ขั้นตรวจทาน transcript แล้ว")
        st.rerun()
