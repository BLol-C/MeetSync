"""แท็บ ④ รายงานการประชุม: ให้ AI ร่าง -> ตรวจ/แก้ (พร้อมคำเตือน) -> อนุมัติ -> PDF / Calendar"""

import datetime
import json

import pandas as pd
import streamlit as st

from bot import botclient
import service
from service import ServiceError
from ui import common
from ui.common import clean_str

ACTION_COLS = ["_ev", "งาน / นัดหมาย", "ผู้รับผิดชอบ", "วันที่", "เวลาเริ่ม", "เวลาสิ้นสุด", "หลักฐานจาก transcript", "Calendar"]
NO_ASSIGNEE = "— ไม่ระบุ —"


def _rev(mid: int) -> int:
    return st.session_state.get(f"rev_report_{mid}", 0)


def _bump(mid: int):
    st.session_state[f"rev_report_{mid}"] = _rev(mid) + 1


# ── แปลงระหว่างเนื้อหารายงานกับตาราง/ช่องกรอก ──

def _to_date(value):
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _to_time(value):
    if not value:
        return None
    try:
        hour, minute = str(value).split(":")[:2]
        return datetime.time(int(hour), int(minute))
    except ValueError:
        return None


def _date_str(value) -> str | None:
    if value is None or value != value or value is pd.NaT:
        return None
    if isinstance(value, (datetime.date, datetime.datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    return clean_str(value) or None


def _time_str(value) -> str | None:
    if value is None or value != value or value is pd.NaT:
        return None
    if isinstance(value, (datetime.time, datetime.datetime, pd.Timestamp)):
        return value.strftime("%H:%M")
    return clean_str(value)[:5] or None


def _evidence_text(evidence: list[str], grounded) -> str:
    quotes = " | ".join(f"“{e}”" for e in evidence)
    if grounded is True:
        return "✓ พบใน transcript  " + quotes
    if grounded is False:
        return "⚠ ไม่พบใน transcript  " + quotes
    return "⚠ ไม่มีข้อความอ้างอิง" if not evidence else quotes


def actions_df(items: list[dict]) -> pd.DataFrame:
    rows = []
    for it in items:
        rows.append({
            "_ev": json.dumps(it.get("evidence") or [], ensure_ascii=False),
            "งาน / นัดหมาย": it["description"], "ผู้รับผิดชอบ": it.get("assignee") or NO_ASSIGNEE,
            "วันที่": _to_date(it.get("due_date")), "เวลาเริ่ม": _to_time(it.get("due_time")),
            "เวลาสิ้นสุด": _to_time(it.get("due_time_end")),
            "หลักฐานจาก transcript": _evidence_text(it.get("evidence") or [], it.get("grounded")),
            "Calendar": "✓ ส่งแล้ว" if it.get("calendar_synced") else "",
        })
    return pd.DataFrame(rows, columns=ACTION_COLS)


def actions_from_df(df: pd.DataFrame) -> list[dict]:
    items = []
    for rec in df.to_dict("records"):
        desc = clean_str(rec.get("งาน / นัดหมาย"))
        if not desc:
            continue
        assignee = clean_str(rec.get("ผู้รับผิดชอบ"))
        try:
            evidence = json.loads(rec.get("_ev")) if isinstance(rec.get("_ev"), str) else []
        except ValueError:
            evidence = []
        items.append({
            "description": desc, "assignee": None if assignee in ("", NO_ASSIGNEE) else assignee,
            "due_date": _date_str(rec.get("วันที่")), "due_time": _time_str(rec.get("เวลาเริ่ม")),
            "due_time_end": _time_str(rec.get("เวลาสิ้นสุด")), "evidence": evidence,
        })
    return items


# ── ส่วนหัวรายงาน (มาจากข้อมูลในระบบ ไม่ใช่ AI) ──

def _header_card(header: dict):
    with st.container(border=True):
        st.caption("ส่วนหัวรายงาน — มาจากข้อมูลในระบบ ไม่ได้มาจาก AI (แก้ที่แท็บ ① ข้อมูล)")
        c1, c2 = st.columns(2)
        c1.markdown(f"**{common.md_escape(header['title'])}**")
        if header["org_name"]:
            c1.caption(common.md_escape(header["org_name"]))
        when = header["date_text"]
        if header["start_time"]:
            when += f" · {header['start_time']}" + (f"–{header['end_time']}" if header["end_time"] else "") + " น."
        c2.markdown(("ครั้งที่ " + common.md_escape(header["meeting_no"]) + " · " if header["meeting_no"] else "") + when
                    + (" · " + common.md_escape(header["venue"]) if header["venue"] else ""))
        a, b = st.columns(2)
        a.markdown(f"ประธาน: **{common.md_escape(header['chair'])}**" if header["chair"] else "ประธาน: ⚠ ยังไม่กำหนด")
        b.markdown(f"เลขา: **{common.md_escape(header['secretary'])}**" if header["secretary"] else "เลขา: ⚠ ยังไม่กำหนด")
        a.markdown("ผู้มาประชุม: " + (", ".join(common.md_escape(p["name"]) for p in header["attendees"]) or "— ไม่มี —"))
        b.markdown("ผู้ไม่มาประชุม: " + (", ".join(
            common.md_escape(p["name"])
            + (f" ({common.md_escape(p['reason'])})" if p.get("reason") else "")
            + (" (ยังไม่ยืนยัน)" if p["unconfirmed"] else "")
            for p in header["absent"]) or "— ไม่มี —"))
        if header.get("guests"):
            a.markdown("ผู้เข้าร่วมประชุม: " + ", ".join(common.md_escape(p["name"]) for p in header["guests"]))


# ── วาระ (การ์ดต่อวาระ แก้ข้อความยาวได้สะดวก) ──

def _agenda_cards(mid: int, content: dict, warnings: list[dict], editable: bool) -> list[dict]:
    """วาดวาระทั้งหมดและคืนค่าที่กรอกอยู่ตอนนี้ — เพิ่ม/ลบวาระจะจำสิ่งที่พิมพ์ค้างไว้ก่อน แล้วค่อยสร้างช่องใหม่"""
    rev = _rev(mid)
    work_key = f"agenda_work_{mid}_{rev}"
    items = st.session_state.get(work_key)
    if items is None:
        items = [{**a, "resolution": a.get("resolution") or ""} for a in content["agenda"]]
    current: list[dict] = []
    remove_at = None
    for i, a in enumerate(items):
        with st.container(border=True):
            top, rm = st.columns([8, 1.2], vertical_alignment="bottom")
            title = top.text_input(f"เรื่องที่ {i + 1}", value=a["title"], key=f"ag_t_{mid}_{rev}_{i}", disabled=not editable,
                                   placeholder="ชื่อเรื่อง")
            section_label = st.selectbox(
                "อยู่ในวาระ", options=list(common.AGENDA_SECTION_BY_LABEL), key=f"ag_s_{mid}_{rev}_{i}", disabled=not editable,
                index=list(common.AGENDA_SECTION_LABEL).index(a.get("section") or "consider_new"),
                help="เรื่องที่ AI สรุปอยู่ที่วาระ 4.2 ย้ายไปวาระ 1 ถ้าเป็นเรื่องแจ้งให้ทราบ — วาระ 2, 3, 4.1 ระบบเติมจากการประชุมครั้งก่อนที่เลือกไว้ (ถ้ามี)")
            section = common.AGENDA_SECTION_BY_LABEL[section_label]
            if editable and rm.button("ลบเรื่อง", key=f"ag_rm_{mid}_{rev}_{i}", width="stretch"):
                remove_at = i
            discussion = st.text_area("สาระสำคัญของการอภิปราย", value=a["discussion"], key=f"ag_d_{mid}_{rev}_{i}",
                                      disabled=not editable, height=110)
            resolution = st.text_area("มติที่ประชุม (เว้นว่าง = เพื่อทราบ / ยังไม่มีมติ)", value=a["resolution"],
                                      key=f"ag_r_{mid}_{rev}_{i}", disabled=not editable, height=70)
            evidence = a.get("evidence") or []
            if section in ("inform", "consider_new") and (evidence or resolution.strip()):   # เฉพาะเรื่องที่ AI สกัดจาก transcript
                st.caption("หลักฐานจาก transcript: " + _evidence_text(evidence, a.get("grounded", True if evidence else None)))
            for w in warnings:
                if w["path"].startswith(f"agenda[{i}]"):
                    st.warning(w["message"])
        current.append({"section": section, "title": title, "discussion": discussion, "resolution": resolution,
                        "evidence": evidence, "grounded": a.get("grounded")})
    if editable:
        add = st.button("＋ เพิ่มเรื่อง", key=f"ag_add_{mid}_{rev}")
        if remove_at is not None or add:
            new_items = [x for j, x in enumerate(current) if j != remove_at]
            if add:
                new_items.append({"section": "consider_new", "title": "", "discussion": "", "resolution": "", "evidence": [],
                                  "grounded": None})
            st.session_state[f"rev_report_{mid}"] = rev + 1
            st.session_state[f"agenda_work_{mid}_{rev + 1}"] = new_items
            st.rerun()
    return current


def _collect(mid: int, summary: str, other: str, agenda: list[dict], actions_edited: pd.DataFrame) -> dict:
    return {
        "summary": summary, "other_matters": other.strip() or None,
        "agenda": [{"section": a["section"], "title": a["title"], "discussion": a["discussion"],
                    "resolution": a["resolution"].strip() or None, "evidence": a["evidence"]}
                   for a in agenda if a["title"].strip() or a["discussion"].strip()],
        "action_items": actions_from_df(actions_edited),
    }


# ── การกระทำ ──

def _generate(user: dict, mid: int):
    with st.spinner("AI กำลังร่างรายงาน… (อาจใช้เวลา 10–60 วินาที)"):
        try:
            out = service.generate_report(user, mid)
        except ServiceError as e:
            st.error(str(e))
            return
    _bump(mid)
    n = len(out["warnings"])
    common.flash("success", "AI ร่างรายงานเสร็จแล้ว — ตรวจและแก้ให้ถูกต้องก่อนอนุมัติ" + (f" (มี {n} รายการที่ควรตรวจ)" if n else ""))
    st.rerun()


@st.dialog("ให้ AI ร่างรายงานใหม่")
def _regenerate_dialog(user: dict, mid: int):
    st.warning("ฉบับร่างปัจจุบัน **รวมสิ่งที่คุณแก้ไว้** จะถูกแทนที่ด้วยฉบับใหม่จาก AI (รายงานมีฉบับเดียวต่อการประชุม)")
    if st.button("ร่างใหม่", type="primary"):
        _generate(user, mid)


@st.dialog("ยืนยันการอนุมัติรายงาน")
def _approve_dialog(user: dict, mid: int, warnings: list[dict]):
    if warnings:
        st.warning("ยังมีรายการที่ควรตรวจก่อนอนุมัติ:")
        for w in warnings:
            st.markdown(f"- {w['message']}")
        ok = st.checkbox("ฉันตรวจแล้ว และต้องการอนุมัติต่อไป")
    else:
        st.success("ไม่พบรายการที่ต้องตรวจ")
        ok = True
    st.caption("อนุมัติแล้วรายงานจะถูกล็อก (ถ้าต้องแก้ ยกเลิกการอนุมัติได้ แล้วอนุมัติใหม่หลังแก้)")
    if st.button("✅ อนุมัติรายงาน", type="primary", disabled=not ok):
        try:
            service.approve_report(user, mid, confirm_warnings=bool(warnings))
        except ServiceError as e:
            st.error(str(e))
            return
        _bump(mid)
        common.flash("success", "อนุมัติรายงานแล้ว — ดาวน์โหลด PDF หรือส่งงานเข้า Calendar ได้")
        st.rerun()


@st.dialog("ยกเลิกการอนุมัติเพื่อแก้รายงาน")
def _reopen_dialog(user: dict, mid: int):
    st.warning("รายงานจะกลับเป็นฉบับร่าง (PDF จะมีลายน้ำ “ฉบับร่าง”) และต้องอนุมัติใหม่หลังแก้ — "
               "งานที่ส่งเข้า Calendar ไปแล้วและไม่ถูกแก้จะไม่ถูกส่งซ้ำ")
    if st.button("ยกเลิกการอนุมัติ", type="primary"):
        try:
            service.reopen_report(user, mid)
        except ServiceError as e:
            st.error(str(e))
            return
        _bump(mid)
        common.flash("info", "รายงานกลับเป็นฉบับร่างแล้ว — แก้ไขได้")
        st.rerun()


def _calendar_card(user: dict, mid: int, report: dict):
    st.subheader("ส่งงานเข้า Google Calendar")
    items = report["content"]["action_items"]
    if not service.calendar_connected(user):
        st.caption("ยังไม่ได้เชื่อมต่อ Google Calendar ของบัญชีนี้ (ขอสิทธิ์สร้างนัดหมายอย่างเดียว แยกจากการล็อกอิน)")
        st.link_button("เชื่อมต่อ Google Calendar", botclient.calendar_connect_url(user, f"{botclient.UI_URL}/?m={mid}"))
        return
    with_date = [i for i in items if i["due_date"]]
    no_date = [i for i in items if not i["due_date"]]
    synced = [i for i in with_date if i["calendar_synced"]]
    st.caption(f"ส่งได้เฉพาะงานที่มีวันที่ ({len(with_date)}/{len(items)} รายการ) ผู้รับผิดชอบที่มีอีเมลในรายชื่อจะถูกเพิ่มเป็นผู้ร่วมงาน "
               "(ไม่ส่งอีเมลเชิญ เว้นแต่ตั้ง CALENDAR_SEND_INVITES=1) งานที่ส่งแล้วไม่ถูกส่งซ้ำ")

    # ผลการกดส่งรอบล่าสุด (เก็บข้ามการ rerun เพื่อให้เห็นตรงนี้ ไม่ใช่ที่หัวหน้าซึ่งอยู่ไกลจากปุ่ม)
    last = st.session_state.pop(f"sync_result_{mid}", None)
    if last:
        if last["sent"]:
            st.success(f"✅ ส่งเข้า Google Calendar เรียบร้อยแล้ว {last['sent']} รายการ")
        for r in last["failed"]:
            st.error(f"ส่งไม่สำเร็จ: {common.md_escape(r['description'])} — {r['error']}")
        if not last["sent"] and not last["failed"]:
            st.info("ไม่มีงานใหม่ให้ส่ง (ส่งครบแล้ว หรือไม่มีงานที่ระบุวันที่)")

    # สถานะปัจจุบัน (ดึงจากฐานข้อมูล เห็นตลอดแม้เปิดหน้านี้ใหม่ภายหลัง)
    if with_date and len(synced) == len(with_date):
        st.success(f"✅ ส่งเข้า Google Calendar แล้วครบ {len(synced)}/{len(with_date)} รายการ")
    elif synced:
        st.info(f"ส่งเข้า Calendar แล้ว {len(synced)}/{len(with_date)} รายการ — ที่เหลือยังไม่ได้ส่ง")
    else:
        st.caption("ยังไม่ได้ส่งงานเข้า Calendar")
    if no_date:
        st.caption("ไม่ได้ส่ง (ไม่มีวันที่กำหนด): " + ", ".join(common.md_escape(i["description"]) for i in no_date))

    pending = [i for i in with_date if not i["calendar_synced"]]
    label = "📅 ส่งงานที่ยังไม่ได้ส่งเข้า Calendar" if synced and pending else "📅 ส่งงานทั้งหมดเข้า Calendar"
    if st.button(label, key=f"sync_{mid}", disabled=not pending):
        with st.spinner("กำลังส่งเข้า Google Calendar…"):
            results = service.sync_all(user, mid)
        st.session_state[f"sync_result_{mid}"] = {
            "sent": sum(1 for r in results if r["ok"] and not r.get("already")),
            "failed": [r for r in results if not r["ok"]],
        }
        _bump(mid)
        st.rerun()


def render(user: dict, detail: dict):
    meeting = detail["meeting"]
    mid = meeting["meeting_id"]
    status = meeting["status"]
    view = service.get_report_view(user, mid)
    report = view["report"]
    header = view["header"]

    if status in ("scheduled", "recording", "transcript_review"):
        st.info("ต้องบันทึกการประชุมและยืนยัน transcript ก่อน (แท็บ ② และ ③) จึงจะให้ AI ร่างรายงานได้")
        if not report:
            return

    _header_card(header)
    for w in view["header_warnings"]:
        if status in ("transcript_verified", "draft") or w["code"] in ("no_chair", "no_secretary"):
            st.warning(w["message"])

    if not report:
        st.subheader("รายงานการประชุม")
        if view["can_generate"]:
            st.write("ให้ AI (Gemini) ร่างรายงานจาก transcript ที่ยืนยันแล้ว — ได้ **ฉบับร่าง** ที่คุณต้องตรวจ/แก้ก่อนอนุมัติ "
                     "รายการที่ AI ไม่แน่ใจ (ผู้รับผิดชอบไม่อยู่ในรายชื่อ, วันที่ผิดปกติ, มติที่อ้างอิงไม่เจอใน transcript) จะมีคำเตือนกำกับ")
            if st.button("✨ ให้ AI ร่างรายงาน", type="primary", key=f"gen_{mid}"):
                _generate(user, mid)
        return

    content = report["content"]
    editable = view["editable"]
    approved = report["approved"]
    warnings = content["warnings"]

    if approved:
        when = report["approved_at"]
        st.success(f"✓ อนุมัติแล้วโดย {common.md_escape(report['approved_by_name'] or '-')} เมื่อ "
                   f"{common.thai_date(when)} {when:%H:%M} น. — รายงานถูกล็อก")
    else:
        st.warning("รายงานฉบับร่าง — ตรวจและแก้ให้ถูกต้องก่อนอนุมัติ (PDF ตอนนี้มีลายน้ำ “ฉบับร่าง”)")
        if warnings:
            with st.expander(f"⚠ รายการที่ควรตรวจก่อนอนุมัติ ({len(warnings)})", expanded=True):
                for w in warnings:
                    st.markdown(f"- {w['message']}")

    if status == "transcript_verified":
        st.info("แก้และยืนยัน transcript ใหม่แล้ว — รายงานด้านล่างสร้างจากข้อความเดิม เลือกได้ว่าจะ “ให้ AI ร่างใหม่” จาก transcript ล่าสุด "
                "(สิ่งที่คุณแก้ในรายงานจะถูกแทนที่) หรือ “ใช้รายงานเดิมต่อ” เพื่อแก้เองและอนุมัติ (ระบบจะเตือนถ้าหลักฐานอ้างอิงไม่ตรงกับ transcript ใหม่)")

    rev = _rev(mid)
    st.subheader("สรุปภาพรวม")
    summary = st.text_area("สรุปภาพรวมการประชุม", value=content["summary"], key=f"sum_{mid}_{rev}", disabled=not editable,
                           height=130, label_visibility="collapsed")
    st.subheader("ระเบียบวาระและมติ (วาระ 1–4)")
    agenda = _agenda_cards(mid, content, warnings, editable)
    if not agenda and not editable:
        st.caption("ไม่มีวาระ")
    st.subheader("วาระ 5 · เรื่องอื่น ๆ")
    other = st.text_area("เรื่องอื่น ๆ", value=content["other_matters"] or "", key=f"other_{mid}_{rev}",
                         disabled=not editable, height=80, label_visibility="collapsed")

    st.subheader("งานที่ได้รับมอบหมาย")
    st.caption("เลือกวันที่/เวลาจากช่อง; กดเครื่องหมาย ＋ ใต้ตารางเพื่อเพิ่มงาน; เลือกแถวแล้วกดถังขยะเพื่อลบ" if editable else "")
    for w in warnings:
        if w["path"].startswith("action_items"):
            st.warning(w["message"])
    names = [NO_ASSIGNEE] + [p["display_name"] for p in view["people"]]
    actions_edit = st.data_editor(
        actions_df(content["action_items"]), key=f"act_{mid}_{rev}", hide_index=True, width="stretch",
        num_rows="dynamic" if editable else "fixed",
        disabled=True if not editable else ["หลักฐานจาก transcript", "Calendar"],
        column_order=ACTION_COLS[1:],
        column_config={
            "งาน / นัดหมาย": st.column_config.TextColumn("งาน / นัดหมาย", width="large"),
            "ผู้รับผิดชอบ": st.column_config.SelectboxColumn("ผู้รับผิดชอบ", options=names, default=NO_ASSIGNEE, width="medium"),
            "วันที่": st.column_config.DateColumn("วันที่", format="YYYY-MM-DD", width="small"),
            "เวลาเริ่ม": st.column_config.TimeColumn("เวลาเริ่ม", format="HH:mm", step=60, width="small"),
            "เวลาสิ้นสุด": st.column_config.TimeColumn("เวลาสิ้นสุด", format="HH:mm", step=60, width="small"),
            "หลักฐานจาก transcript": st.column_config.TextColumn("หลักฐานจาก transcript", width="large"),
            "Calendar": st.column_config.TextColumn("Calendar", width="small"),
        },
    )

    snapshot = report["ai_snapshot"]
    if snapshot and not approved:
        with st.expander("ดูฉบับที่ AI ร่างไว้เดิม (เทียบกับที่แก้)"):
            st.markdown("**สรุป:** " + common.md_escape(snapshot.get("summary") or ""))
            for i, a in enumerate(snapshot.get("agenda") or [], start=1):
                st.markdown(f"**วาระ {i}: {common.md_escape(a.get('title') or '')}**  \n{common.md_escape(a.get('discussion') or '')}  \n"
                            f"มติ: {common.md_escape(a.get('resolution') or '—')}")

    st.divider()
    b1, b2, b3, b4 = st.columns([1.3, 1.5, 1.4, 1.5])
    if editable:
        if b1.button("💾 บันทึกร่าง", key=f"savedraft_{mid}"):
            try:
                service.save_report_draft(user, mid, _collect(mid, summary, other, agenda, actions_edit))
            except ServiceError as e:
                st.error(str(e))
            else:
                _bump(mid)
                common.flash("success", "บันทึกร่างแล้ว")
                st.rerun()
        if b2.button("✨ ให้ AI ร่างใหม่", key=f"regen_{mid}"):
            _regenerate_dialog(user, mid)
        if b3.button("✅ อนุมัติรายงาน…", key=f"approve_{mid}", type="primary"):
            try:   # บันทึกที่แก้ค้างไว้ก่อนเสมอ แล้วเอาคำเตือนล่าสุดของสิ่งที่เห็นอยู่มาให้ยืนยัน
                cleaned = service.save_report_draft(user, mid, _collect(mid, summary, other, agenda, actions_edit))
            except ServiceError as e:
                st.error(str(e))
            else:
                _approve_dialog(user, mid, cleaned["warnings"] + view["header_warnings"])
    elif status == "transcript_verified":
        if b1.button("✨ ให้ AI ร่างใหม่", key=f"regen_{mid}"):
            _regenerate_dialog(user, mid)
        if b2.button("➡ ใช้รายงานเดิมต่อ", key=f"keep_{mid}", type="primary"):
            try:
                service.keep_existing_report(user, mid)
            except ServiceError as e:
                st.error(str(e))
            else:
                common.flash("info", "ใช้รายงานเดิมต่อ — ตรวจ แก้ไข และอนุมัติได้เลย")
                st.rerun()
    elif approved:
        if b1.button("✎ ยกเลิกการอนุมัติเพื่อแก้", key=f"reopen_rep_{mid}"):
            _reopen_dialog(user, mid)
    try:
        pdf, name = service.build_pdf(user, mid)
        b4.download_button("⬇ ดาวน์โหลด PDF" + ("" if approved else " (ฉบับร่าง)"), data=pdf, file_name=name,
                           mime="application/pdf", key=f"pdf_{mid}")
    except ServiceError as e:
        b4.caption(str(e))

    if approved:
        st.divider()
        _calendar_card(user, mid, report)
