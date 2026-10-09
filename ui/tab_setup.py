"""แท็บ ① ข้อมูลการประชุมและผู้เข้าร่วม/บทบาท"""

import pandas as pd
import streamlit as st

import service
from service import ServiceError
from ui import common
from ui.common import ATT_BY_LABEL, ATT_LABEL, ROLE_BY_LABEL, ROLE_LABEL, SOURCE_LABEL, clean_str

COLS = ["speaker_id", "ชื่อ", "ชื่อใน Meet", "อีเมล", "บทบาท", "ตำแหน่ง", "การเข้าร่วม", "สาเหตุที่ไม่มา", "ที่มา", "ข้อความที่พูด"]
VISIBLE = ["ชื่อ", "ชื่อใน Meet", "อีเมล", "บทบาท", "ตำแหน่ง", "การเข้าร่วม", "สาเหตุที่ไม่มา", "ที่มา", "ข้อความที่พูด"]


def _rev(mid: int) -> int:
    return st.session_state.get(f"rev_people_{mid}", 0)


def _people_df(people: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "speaker_id": p["speaker_id"], "ชื่อ": p["display_name"], "ชื่อใน Meet": p.get("meet_alias") or "", "อีเมล": p["email"] or "",
            "บทบาท": ROLE_LABEL[p["role"]], "ตำแหน่ง": p.get("position") or "", "การเข้าร่วม": ATT_LABEL[p["attendance"]],
            "สาเหตุที่ไม่มา": p.get("absence_reason") or "",
            "ที่มา": SOURCE_LABEL.get(p["source"], p["source"]), "ข้อความที่พูด": p["segment_count"],
        } for p in people],
        columns=COLS,
    )


def people_rows(df: pd.DataFrame) -> list[dict]:
    """ตารางผู้เข้าร่วมที่แก้ในหน้าเว็บ -> แถวสำหรับ service.apply_people_table"""
    rows = []
    for rec in df.to_dict("records"):
        sid = rec.get("speaker_id")
        rows.append({
            "speaker_id": None if sid is None or sid != sid else int(sid),
            "display_name": clean_str(rec.get("ชื่อ")),
            "meet_alias": clean_str(rec.get("ชื่อใน Meet")),
            "email": clean_str(rec.get("อีเมล")),
            "role": ROLE_BY_LABEL.get(clean_str(rec.get("บทบาท")), "attendee"),
            "attendance": ATT_BY_LABEL.get(clean_str(rec.get("การเข้าร่วม")), "invited"),
            "absence_reason": clean_str(rec.get("สาเหตุที่ไม่มา")),
            "position": clean_str(rec.get("ตำแหน่ง")),
        })
    return rows


def render(user: dict, detail: dict):
    meeting = detail["meeting"]
    mid = meeting["meeting_id"]
    status = meeting["status"]
    locked = status == "approved"

    st.subheader("ข้อมูลการประชุม")
    if locked:
        st.caption("🔒 รายงานอนุมัติแล้ว แก้ข้อมูลไม่ได้ (ถ้าต้องแก้ ให้ยกเลิกการอนุมัติที่แท็บ ④ รายงาน)")
    with st.form(f"info_{mid}"):
        url = st.text_input("ลิงก์ Google Meet", value=meeting["meet_url"] or "",
                            disabled=locked or status != "scheduled", help="เปลี่ยนลิงก์ได้เฉพาะก่อนเริ่มบอท")
        c1, c2 = st.columns(2)
        title = c1.text_input("ชื่อเรื่องการประชุม", value=meeting["title"] or "", disabled=locked)
        org = c2.text_input("หน่วยงาน", value=meeting["org_name"] or "", disabled=locked)
        no = c1.text_input("ครั้งที่", value=meeting["meeting_no"] or "", disabled=locked)
        venue = c2.text_input("สถานที่", value=meeting["venue"] or "", disabled=locked)
        previous = common.previous_meeting_select(
            user, key=f"prev_{mid}", current_id=meeting.get("previous_meeting_id"), exclude_id=mid, disabled=locked)
        saved = st.form_submit_button("บันทึกข้อมูล", disabled=locked)
    if saved:
        fields = {"title": title, "org_name": org, "meeting_no": no, "venue": venue, "previous_meeting_id": previous}
        if status == "scheduled":
            fields["meet_url"] = url
        try:
            service.update_meeting(user, mid, **fields)
        except ServiceError as e:
            st.error(str(e))
        else:
            common.flash("success", "บันทึกข้อมูลการประชุมแล้ว")
            st.rerun()

    st.subheader("ผู้เข้าร่วมและบทบาท")
    st.caption("ใส่ชื่อ **ตรงกับชื่อที่แสดงใน Google Meet** ระบบจะจับคู่คนพูดกับรายชื่อนี้ให้อัตโนมัติ  "
               "ถ้าชื่อใน Meet ไม่ตรง (เช่น มีเลขหรือชื่อเล่นพ่วง) ให้ “รวมชื่อ” ที่แท็บ ③ Transcript  "
               "ประธาน/เลขาที่ใส่อีเมลไว้ เข้ามาตรวจและอนุมัติรายงานได้ด้วยบัญชี Google ของตน  "
               "“การเข้าร่วม”: คนที่มีเสียงพูดในประชุมถูกตั้งเป็น “เข้าร่วม” ให้เอง ส่วนคนที่เข้าฟังเฉยๆ ให้ตั้งเอง "
               "(ที่ยังเป็น “ยังไม่ยืนยัน” จะถูกนับเป็นผู้ไม่มาประชุมในรายงาน)")
    for w in detail["header_warnings"]:
        if w["code"] in ("no_chair", "no_secretary"):
            st.warning(w["message"])

    df = _people_df(detail["people"])
    edited = st.data_editor(
        df, key=f"people_{mid}_{_rev(mid)}", hide_index=True, width="stretch",
        num_rows="fixed" if locked else "dynamic",
        disabled=True if locked else ["ที่มา", "ข้อความที่พูด"],
        column_order=VISIBLE,
        column_config={
            "ชื่อ": st.column_config.TextColumn("ชื่อ", width="medium"),
            "ชื่อใน Meet": st.column_config.TextColumn("ชื่อใน Meet", help="ชื่อที่ Google Meet แสดงของคนนี้ (เช่น ชื่อบัญชีภาษาอังกฤษ) ไว้จับคู่คนพูดและรายชื่อในห้องให้อัตโนมัติ ไม่ขึ้นในรายงาน — ว่างไว้ได้ ถ้าชื่อที่ลงทะเบียนตรงกับใน Meet", width="medium"),
            "อีเมล": st.column_config.TextColumn("อีเมล", width="medium", help="ใช้เชิญเข้า Calendar และให้ประธาน/เลขาอนุมัติได้"),
            "บทบาท": st.column_config.SelectboxColumn("บทบาท", options=list(ROLE_BY_LABEL), default=ROLE_LABEL["attendee"], required=True),
            "ตำแหน่ง": st.column_config.TextColumn("ตำแหน่ง", help="ตำแหน่งของผู้เข้าร่วม แสดงในคอลัมน์ ตำแหน่ง ของรายงาน PDF (เว้นว่าง = ใช้บทบาทในที่ประชุมแทน)", width="medium"),
            "การเข้าร่วม": st.column_config.SelectboxColumn("การเข้าร่วม", options=list(ATT_BY_LABEL), default="ยังไม่ยืนยัน", required=True),
            "สาเหตุที่ไม่มา": st.column_config.TextColumn("สาเหตุที่ไม่มา", help="แสดงในวงเล็บท้ายชื่อในรายการผู้ไม่มาประชุม เช่น ติดภารกิจ, ลาป่วย"),
            "ที่มา": st.column_config.TextColumn("ที่มา", help="ลงทะเบียนไว้เอง หรือระบบพบชื่อนี้จากคำบรรยายใน Meet"),
            "ข้อความที่พูด": st.column_config.NumberColumn("ข้อความที่พูด", format="%d ช่วง"),
        },
    )
    if meeting.get("previous_meeting_id") and not locked:
        if st.button("📋 เติมรายชื่อจากการประชุมครั้งก่อน", key=f"copy_roster_{mid}",
                     help="เพิ่มผู้เข้าร่วมของการประชุมครั้งก่อนที่ยังไม่มีในรายชื่อนี้ พร้อมอีเมล บทบาท ตำแหน่ง และ \"ชื่อใน Meet\" "
                          "(ไม่คัดลอกสถานะมา/ไม่มา)"):
            try:
                added = service.copy_roster_into_meeting(user, mid)
            except ServiceError as e:
                common.flash("error", str(e))
            else:
                common.flash("success" if added else "info", f"เพิ่มรายชื่อจากครั้งก่อน {added} คน" if added else "ไม่มีคนใหม่ให้เพิ่ม — มีครบแล้ว")
            st.session_state[f"rev_people_{mid}"] = _rev(mid) + 1
            st.rerun()
    if st.button("💾 บันทึกรายชื่อ", key=f"save_people_{mid}", disabled=locked):
        result = service.apply_people_table(user, mid, people_rows(edited))
        st.session_state[f"rev_people_{mid}"] = _rev(mid) + 1
        parts = [f"{label} {result[k]} คน" for k, label in (("added", "เพิ่ม"), ("updated", "แก้"), ("deleted", "ลบ")) if result[k]]
        if parts:
            common.flash("success", "บันทึกรายชื่อแล้ว: " + ", ".join(parts))
        elif not result["errors"]:
            common.flash("info", "ไม่มีการเปลี่ยนแปลง")
        for err in result["errors"]:
            common.flash("error", err)
        st.rerun()

    if status == "scheduled" and not locked:
        with st.expander("ลบการประชุมนี้"):
            st.caption("ลบได้เฉพาะการประชุมที่ยังไม่เคยเริ่มบอท (รายชื่อผู้เข้าร่วมจะถูกลบไปด้วย)")
            sure = st.checkbox("ยืนยันว่าต้องการลบ", key=f"del_ok_{mid}")
            if st.button("ลบการประชุม", key=f"del_{mid}", disabled=not sure, type="primary"):
                try:
                    service.delete_meeting(user, mid)
                except ServiceError as e:
                    st.error(str(e))
                else:
                    common.flash("success", "ลบการประชุมแล้ว")
                    common.go("home")
