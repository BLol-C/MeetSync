"""หน้าสร้างการประชุมใหม่: ข้อมูลหัวรายงาน + ผู้เข้าร่วมและบทบาท (บอทยังไม่ถูกสั่ง — สั่งที่หน้าการประชุม)"""

import pandas as pd
import streamlit as st

import service
from service import ServiceError
from ui import common
from ui.common import ATT_BY_LABEL, ROLE_BY_LABEL, ROLE_LABEL, clean_str

PEOPLE_COLS = ["ชื่อภาษาไทย", "ชื่อภาษาอังกฤษ", "อีเมล", "บทบาท", "ตำแหน่ง", "การเข้าร่วม", "สาเหตุที่ไม่มา"]


def _default_people() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ชื่อภาษาไทย": "", "ชื่อภาษาอังกฤษ": "", "อีเมล": "", "บทบาท": "ประธาน", "ตำแหน่ง": "", "การเข้าร่วม": "เข้าร่วม", "สาเหตุที่ไม่มา": ""},
            {"ชื่อภาษาไทย": "", "ชื่อภาษาอังกฤษ": "", "อีเมล": "", "บทบาท": "เลขา", "ตำแหน่ง": "", "การเข้าร่วม": "เข้าร่วม", "สาเหตุที่ไม่มา": ""},
        ],
        columns=PEOPLE_COLS,
    )


def people_from_df(df: pd.DataFrame) -> list[dict]:
    people = []
    for rec in df.to_dict("records"):
        name = clean_str(rec.get("ชื่อภาษาไทย"))
        if not name:
            continue
        people.append({
            "display_name": name,
            "meet_alias": clean_str(rec.get("ชื่อภาษาอังกฤษ")) or None,
            "email": clean_str(rec.get("อีเมล")) or None,
            "role": ROLE_BY_LABEL.get(clean_str(rec.get("บทบาท")), "attendee"),
            "attendance": ATT_BY_LABEL.get(clean_str(rec.get("การเข้าร่วม")), "invited"),
            "absence_reason": clean_str(rec.get("สาเหตุที่ไม่มา")) or None,
            "position": clean_str(rec.get("ตำแหน่ง")) or None,
        })
    return people


def people_column_config() -> dict:
    return {
        "ชื่อภาษาไทย": st.column_config.TextColumn("ชื่อภาษาไทย", help="ชื่อที่จะขึ้นในรายงาน PDF (ภาษาไทย)", required=False, width="medium"),
        "ชื่อภาษาอังกฤษ": st.column_config.TextColumn("ชื่อภาษาอังกฤษ", help="ชื่อที่ Google Meet แสดงของคนนี้ (เช่น ชื่อบัญชีมหาวิทยาลัยที่เป็นภาษาอังกฤษ) ไว้จับคู่คนพูดและรายชื่อในห้องให้อัตโนมัติ ไม่ขึ้นในรายงาน — เว้นว่างได้ ถ้าชื่อภาษาไทยตรงกับที่ Meet แสดงอยู่แล้ว", width="medium"),
        "อีเมล": st.column_config.TextColumn("อีเมล", help="ใช้เชิญเข้า Calendar และให้ประธาน/เลขาเข้าอนุมัติรายงานได้", width="medium"),
        "บทบาท": st.column_config.SelectboxColumn("บทบาท", options=list(ROLE_BY_LABEL), default=ROLE_LABEL["attendee"], required=True),
        "ตำแหน่ง": st.column_config.TextColumn("ตำแหน่ง", help="ตำแหน่งของผู้เข้าร่วม แสดงในคอลัมน์ ตำแหน่ง ของรายงาน PDF (เว้นว่าง = ใช้บทบาทในที่ประชุมแทน)", width="medium"),
        "การเข้าร่วม": st.column_config.SelectboxColumn("การเข้าร่วม", options=list(ATT_BY_LABEL), default="ยังไม่ยืนยัน", required=True),
        "สาเหตุที่ไม่มา": st.column_config.TextColumn("สาเหตุที่ไม่มา", help="เฉพาะผู้ที่ไม่มา — แสดงในวงเล็บท้ายชื่อในรายงาน"),
    }


def _create(user: dict, payload: dict, force: bool = False):
    dups = service.meetings_with_url(user, payload["meet_url"])
    if dups and not force:
        st.session_state["_dup_pending"] = {"payload": payload, "dups": dups}
        st.rerun()
    try:
        meeting_id = service.create_meeting(user, **payload)
    except ServiceError as e:
        st.error(str(e))
        return
    st.session_state.pop("_dup_pending", None)
    common.flash("success", "สร้างการประชุมแล้ว — ตรวจรายชื่อผู้เข้าร่วม แล้วไปแท็บ “② บอท” เพื่อเริ่มบอทเมื่อถึงเวลาประชุม")
    common.go("meeting", meeting_id)


def _render_duplicate_choice(user: dict):
    pending = st.session_state["_dup_pending"]
    st.title("สร้างการประชุมใหม่")
    st.warning(
        f"คุณมีการประชุมที่ใช้ลิงก์ Google Meet นี้อยู่แล้ว {len(pending['dups'])} รายการ — "
        "ห้องเดิมที่ใช้ประชุมซ้ำได้ แต่ถ้าตั้งใจจะใช้ข้อมูลเดิมต่อ ให้เปิดอันเดิมแทนการสร้างใหม่ เพื่อไม่ให้มีรายการซ้ำกัน"
    )
    for d in pending["dups"][:5]:
        with st.container(border=True):
            a, b = st.columns([5, 1.2], vertical_alignment="center")
            a.markdown(f"**{common.md_escape(d['title'] or '(ไม่ได้ตั้งชื่อ)')}**  \n"
                       f"{common.STATUS_LABEL.get(d['status'], d['status'])} · {common.thai_date(d['started_at'])}")
            if b.button("เปิดอันนี้", key=f"dup_open_{d['meeting_id']}"):
                st.session_state.pop("_dup_pending", None)
                common.go("meeting", d["meeting_id"])
    c1, c2 = st.columns(2)
    if c1.button("สร้างการประชุมใหม่อยู่ดี", type="primary", width="stretch"):
        _create(user, pending["payload"], force=True)
    if c2.button("ยกเลิก", width="stretch"):
        st.session_state.pop("_dup_pending", None)
        st.rerun()


def render(user: dict):
    if "_dup_pending" in st.session_state:
        _render_duplicate_choice(user)
        return

    st.title("สร้างการประชุมใหม่")
    with st.form("new_meeting"):
        url = st.text_input("ลิงก์ Google Meet *", placeholder="https://meet.google.com/abc-defg-hij")
        c1, c2 = st.columns(2)
        title = c1.text_input("ชื่อเรื่องการประชุม", help="เว้นว่างได้ ระบบจะตั้งชื่อให้จากวันและเวลา")
        org = c2.text_input("หน่วยงาน", placeholder="เช่น ภาควิชาวิทยาการคอมพิวเตอร์")
        no = c1.text_input("ครั้งที่", placeholder="เช่น 3/2569")
        venue = c2.text_input("สถานที่", placeholder="เช่น ห้องประชุม 2 / ออนไลน์ (Google Meet)")
        previous = common.previous_meeting_select(user, key="new_previous_meeting", default_latest=True)
        st.markdown("##### ผู้เข้าร่วมและบทบาท")
        people_df = st.data_editor(
            _default_people(), key="new_people", num_rows="dynamic", hide_index=True,
            column_config=people_column_config(), width="stretch",
        )
        submitted = st.form_submit_button("สร้างการประชุม", type="primary")

    if submitted:
        if not url.strip():
            st.error("ต้องใส่ลิงก์ Google Meet")
            return
        _create(user, {
            "meet_url": url, "title": title, "org_name": org, "meeting_no": no, "venue": venue,
            "people": people_from_df(people_df), "previous_meeting_id": previous,
        })
