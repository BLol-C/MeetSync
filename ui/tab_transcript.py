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


def _unconfirmed_section(user: dict, people: list[dict], editable: bool):
    """ผู้ที่ลงทะเบียนไว้แต่ยังไม่ยืนยันการเข้าร่วม — เลือกว่าตรงกับชื่อไหนที่เห็นใน Meet (บอทอ่านจากห้อง/จากคนที่พูด)
    หรือกำหนดเองว่ามา/ไม่มา; ถ้าไม่ตั้ง ตอนยืนยัน transcript จะถูกบันทึกเป็น "ไม่มา" """
    pending = [p for p in people if p["source"] == "registered" and p["attendance"] == "invited"]
    if not pending:
        return
    seen = [p for p in people if p["source"] == "meet"]
    labels = {}
    for m in seen:
        labels[f"meet:{m['speaker_id']}"] = (f"{m['display_name']} — พูด {m['segment_count']} ช่วง" if m["segment_count"]
                                             else f"{m['display_name']} — อยู่ในห้อง")
    labels["present"] = "มาประชุม (ไม่พบชื่อในรายการ Meet)"
    labels["absent"] = "ไม่ได้เข้าประชุม (ไม่มา)"
    st.subheader("ผู้เข้าร่วมที่ยังไม่ยืนยันการเข้าร่วม")
    st.caption("บอทอ่านชื่อคนที่อยู่ในห้อง Meet ไว้ให้เลือก เลือกชื่อที่ตรงกับแต่ละคน (ระบบจำชื่อนี้ไว้ ครั้งหน้าจับคู่ให้เอง) "
               "หรือกำหนดเองว่ามา/ไม่มา — ถ้าไม่ตั้ง ตอนกดยืนยัน transcript ระบบจะบันทึกเป็น “ไม่มา”")
    if not seen:
        st.info("ยังไม่พบชื่อใน Meet ที่ไม่ตรงกับรายชื่อ (บอทอ่านรายชื่อในห้องไม่ได้ หรือทุกคนที่อยู่ในห้องจับคู่ได้แล้ว)")
    for p in pending:
        with st.container(border=True):
            a, b, c = st.columns([3, 4, 1.2], vertical_alignment="center")
            a.markdown(f"**{common.md_escape(p['display_name'])}**  \n{common.ROLE_LABEL.get(p['role'], p['role'])}")
            choice = b.selectbox("ตรงกับ", options=list(labels), format_func=labels.get, index=None,
                                 placeholder="เลือกชื่อใน Meet ที่ตรงกัน หรือกำหนดสถานะ…", key=f"unc_{p['speaker_id']}",
                                 label_visibility="collapsed", disabled=not editable)
            if c.button("ยืนยัน", key=f"unc_go_{p['speaker_id']}", disabled=not editable or choice is None, width="stretch"):
                try:
                    if choice in ("present", "absent"):
                        service.set_person_attendance(user, p["speaker_id"], choice)
                        done = "ตั้งเป็น " + ("เข้าร่วม" if choice == "present" else "ไม่มา") + f": {p['display_name']}"
                    else:
                        moved = service.merge_people(user, int(choice.split(":")[1]), p["speaker_id"])
                        done = f"จับคู่แล้ว: {p['display_name']} เข้าร่วม" + (f" (ย้าย {moved} ช่วง)" if moved else "")
                except ServiceError as e:
                    st.error(str(e))
                else:
                    common.flash("success", done)
                    st.session_state[f"rev_segs_{p['meeting_id']}"] = _rev(p["meeting_id"]) + 1
                    st.rerun()


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
    _unconfirmed_section(user, people_in_meeting, editable and status != "approved")

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
            df, key=f"segs_{mid}_{_rev(mid)}", hide_index=True, width="stretch", height=520,
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
        if mid_col.button("✓ ยืนยัน transcript (ปลดล็อกให้ AI ร่างรายงาน)", key=f"verify_{mid}", type="primary"):
            if save_edits():   # ยืนยันสิ่งที่เห็นอยู่ในตารางตอนนี้ — บันทึกที่แก้ค้างไว้ก่อนเสมอ ไม่ทิ้งการแก้ไขโดยไม่รู้ตัว
                try:
                    out = service.verify_transcript(user, mid, bot_running=bot["running_here"])
                except ServiceError as e:
                    st.error(str(e))
                else:
                    if out["unmapped"]:
                        common.flash("warning", "ยืนยันแล้ว แต่ยังมีชื่อจาก Meet ที่ไม่ได้รวมกับผู้เข้าร่วม: "
                                     + ", ".join(out["unmapped"]) + " (AI จะระบุผู้รับผิดชอบงานให้คนเหล่านี้ไม่ได้)")
                    else:
                        common.flash("success", "ยืนยัน transcript แล้ว — ไปแท็บ ④ รายงาน เพื่อให้ AI ร่างรายงาน")
                    if out["marked_absent"]:
                        common.flash("info", "ผู้ที่ยังไม่ยืนยันการเข้าร่วมถูกบันทึกเป็น \"ไม่มา\": "
                                     + ", ".join(out["marked_absent"]) + " (ถ้ามาจริงแต่ไม่ได้พูด แก้เป็น \"เข้าร่วม\" ที่แท็บ ① ข้อมูล)")
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
