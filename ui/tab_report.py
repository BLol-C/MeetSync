"""แท็บ ④ รายงานการประชุม: ให้ AI ร่าง -> ตรวจ/แก้ (พร้อมคำเตือน) -> อนุมัติ -> PDF (ส่งเข้า Calendar อยู่แท็บ ⑤)"""

import datetime
import json

import pandas as pd
import streamlit as st

import service
from reports import history
from service import ServiceError
from ui import common
from ui.common import clean_str

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
            "due_date": _date_str(rec.get("วันที่เริ่ม")), "due_date_end": _date_str(rec.get("วันที่สิ้นสุด")),
            "due_time": _time_str(rec.get("เวลาเริ่ม")), "due_time_end": _time_str(rec.get("เวลาสิ้นสุด")), "evidence": evidence,
        })
    return items


# ── ส่วนหัวรายงาน (มาจากข้อมูลในระบบ ไม่ใช่ AI) ──

def _ops_md(ops: list[tuple[str, str]]) -> str:
    """ผลเทียบข้อความ -> markdown: ส่วนที่ถูกลบเป็นสีแดงขีดฆ่า ส่วนที่เพิ่มเป็นสีเขียว"""
    out = []
    for op, text in ops:
        esc = common.md_escape(text)
        out.append(esc if op == "equal" else f":red[~~{esc}~~]" if op == "del" else f":green[{esc}]")
    return "".join(out)


def _version_label(v: dict) -> str:
    at = v["saved_at"]
    now = " · ใช้อยู่" if v.get("is_current") else ""
    return f"ร่างฉบับที่ {v['version_no']} · {history.KIND_LABEL.get(v['kind'], history.OTHER_KIND)} · {common.thai_date(at)} {at:%H:%M}{now}"


def _show_content(c: dict):
    """ร่างฉบับแรกสุด (ไม่มีฉบับก่อนหน้าให้เทียบ) แสดงเนื้อหาทั้งฉบับ"""
    st.markdown("**สรุปภาพรวม**")
    st.markdown(common.md_escape(c["summary"]))
    for a in c["agenda"]:
        st.markdown(f"**{common.AGENDA_SECTION_LABEL.get(a['section'], a['section'])}:** {common.md_escape(a['title'])}  \n"
                    f"สาระสำคัญ: {common.md_escape(a['discussion'])}  \nมติ: {common.md_escape(a['resolution'] or '—')}")
    if c.get("other_matters"):
        st.markdown("**วาระ 5 · เรื่องอื่น ๆ**")
        st.markdown(common.md_escape(c["other_matters"]))
    for it in c["action_items"]:
        when = " ".join(x for x in ((it["due_date"] or "") + (f" ถึง {it['due_date_end']}" if it.get("due_date_end") else ""),
                                    it["due_time"] and (it["due_time"] + (f"–{it['due_time_end']}" if it["due_time_end"] else ""))) if x)
        st.markdown(f"**งาน:** {common.md_escape(it['description'])}  \nผู้รับผิดชอบ: {common.md_escape(it['assignee'] or '—')}"
                    f"  \nกำหนด: {common.md_escape(when or '—')}")


def _show_diff(d: dict):
    if not d["changed"]:
        st.info("ร่างฉบับนี้ไม่ได้เปลี่ยนเนื้อหาจากฉบับก่อนหน้า")
        return
    if any(op != "equal" for op, _ in d["summary"]):
        st.markdown("**สรุปภาพรวม**")
        st.markdown(_ops_md(d["summary"]))
    tag = {"added": "เพิ่มใหม่", "removed": "ถูกลบ", "changed": "แก้ไข"}
    for a in d["agenda"]:
        if a["status"] == "same":
            continue
        where = common.AGENDA_SECTION_LABEL.get(a["section"], a["section"])
        moved = (f" · ย้ายจาก {common.AGENDA_SECTION_LABEL.get(a['old_section'], a['old_section'])}"
                 if a["section_changed"] else "")
        st.markdown(f"**{where}{moved} — {tag[a['status']]}:** {_ops_md(a['title'])}")
        if any(op != "equal" for op, _ in a["discussion"]):
            st.markdown("สาระสำคัญ: " + _ops_md(a["discussion"]))
        if any(op != "equal" for op, _ in a["resolution"]):
            st.markdown("มติ: " + _ops_md(a["resolution"]))
    if any(op != "equal" for op, _ in d["other_matters"]):
        st.markdown("**วาระ 5 · เรื่องอื่น ๆ**")
        st.markdown(_ops_md(d["other_matters"]))
    for it in d["action_items"]:
        if it["status"] == "same":
            continue
        line = f"**งาน — {tag[it['status']]}:** {_ops_md(it['description'])}"
        if any(op != "equal" for op, _ in it["assignee"]):
            line += "  \nผู้รับผิดชอบ: " + _ops_md(it["assignee"])
        if any(op != "equal" for op, _ in it["when"]):
            line += "  \nกำหนด: " + _ops_md(it["when"])
        st.markdown(line)


@st.dialog("ใช้ร่างฉบับนี้")
def _use_dialog(user: dict, mid: int, no: int):
    st.info(f"ฉบับที่ใช้อยู่จะเปลี่ยนเป็นร่างฉบับที่ {no} ทั้งฉบับ (สรุป วาระ มติ เรื่องอื่น ๆ และงาน) "
            "ไม่มีร่างฉบับใดถูกลบ เลือกกลับไปใช้ฉบับอื่นได้อีก")
    if st.session_state.get(f"report_unsaved_{mid}"):
        st.error("มีการแก้ไขที่ยังไม่ได้บันทึกอยู่ในหน้านี้ ส่วนที่ยังไม่ได้บันทึกจะหายไป (ไม่ถูกเก็บเป็นร่างฉบับ)")
    if st.button(f"ใช้ร่างฉบับที่ {no}", type="primary", key=f"use_ok_{mid}"):
        try:
            changed = service.use_report_version(user, mid, no)
        except ServiceError as e:
            st.error(str(e))
            return
        _bump(mid)
        common.flash("success" if changed else "info",
                     f"ใช้ร่างฉบับที่ {no} แล้ว" if changed else f"ร่างฉบับที่ {no} ใช้อยู่แล้ว")
        st.rerun()


def _history_panel(user: dict, mid: int, editable: bool = False):
    """ประวัติการแก้ไขรายงาน: ทุกครั้งที่ AI ร่าง/คนบันทึกแก้/อนุมัติ/ยกเลิกอนุมัติ เก็บเป็นร่างฉบับหนึ่ง ไม่ทับของเดิม
    เลือกดูย้อนหลังได้ว่าฉบับไหนแก้อะไรจากฉบับก่อนหน้า และเลือกฉบับเก่ามาใช้ได้ (ไม่สร้างฉบับใหม่ แค่ย้ายว่าใช้ฉบับไหนอยู่)"""
    versions = service.report_history(user, mid)
    if not versions:
        return
    current = next((v["version_no"] for v in versions if v.get("is_current")), versions[0]["version_no"])
    with st.expander(f"ประวัติการแก้ไข · ใช้ร่างฉบับที่ {current} (จากทั้งหมด {len(versions)} ฉบับ)"):
        st.dataframe(
            pd.DataFrame([{"ร่างฉบับที่": v["version_no"], "ชนิด": history.KIND_LABEL.get(v["kind"], history.OTHER_KIND),
                           "เมื่อ": f"{common.thai_date(v['saved_at'])} {v['saved_at']:%H:%M}",
                           "สถานะ": "ใช้อยู่" if v.get("is_current") else ""}
                          for v in versions]),
            hide_index=True, width="stretch", height=common.table_height(len(versions), max_px=260))
        if len(versions) < 2:
            return
        nos = [v["version_no"] for v in versions]            # ใหม่สุดก่อน
        label = {v["version_no"]: _version_label(v) for v in versions}
        # เลือกฉบับเดียว แล้วแสดงว่าฉบับนั้นแก้อะไรจากฉบับก่อนหน้า (ฉบับแรกสุดแสดงเนื้อหาทั้งฉบับ)
        # key มีจำนวนฉบับและฉบับที่ใช้อยู่ด้วย: มีฉบับใหม่/เปลี่ยนฉบับที่ใช้แล้วให้กลับมาเริ่มที่ฉบับที่ใช้อยู่
        no = st.selectbox("ดูย้อนหลัง", nos, index=nos.index(current), format_func=label.get,
                          key=f"hist_{mid}_{len(nos)}_{current}")
        if no == nos[-1]:
            _show_content(service.report_version(user, mid, no))
        else:
            before = nos[nos.index(no) + 1]                  # ฉบับก่อนหน้าที่มีอยู่จริง (เลขไม่จำเป็นต้องต่อเนื่อง)
            _show_diff(history.diff_reports(service.report_version(user, mid, before), service.report_version(user, mid, no)))
        if editable and no != current:                       # ฉบับที่ใช้อยู่แล้วไม่ต้องเลือก; อนุมัติแล้วถูกล็อก เลือกใช้ฉบับอื่นไม่ได้
            if st.button(f"ใช้ร่างฉบับที่ {no}", key=f"use_{mid}"):
                _use_dialog(user, mid, no)


def _header_card(header: dict):
    with st.container(border=True):
        st.caption("ส่วนหัวรายงาน")
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


def _agenda_key(items: list[dict]) -> list[tuple]:
    """วาระในรูปที่เทียบกันได้ (ตัดช่องว่าง ข้ามเรื่องที่ว่างทั้งชื่อและเนื้อหา — ตรงกับที่ _collect จะบันทึกจริง)"""
    return [(a["section"], (a["title"] or "").strip(), (a["discussion"] or "").strip(), (a.get("resolution") or "").strip())
            for a in items if (a["title"] or "").strip() or (a["discussion"] or "").strip()]


def has_unsaved(content: dict, summary: str, other: str, agenda: list[dict]) -> bool:
    """สิ่งที่พิมพ์ค้างอยู่ในช่องของแท็บ ④ ต่างจากที่บันทึกไว้หรือไม่ (ผู้ใช้ต้องกด "บันทึกร่าง" ก่อนไปขั้นอนุมัติ)"""
    return (summary.strip() != (content["summary"] or "").strip()
            or (other or "").strip() != (content["other_matters"] or "").strip()
            or _agenda_key(agenda) != _agenda_key(content["agenda"]))


def _collect(mid: int, summary: str, other: str, agenda: list[dict], action_items: list[dict]) -> dict:
    return {
        "summary": summary, "other_matters": other.strip() or None,
        "agenda": [{"section": a["section"], "title": a["title"], "discussion": a["discussion"],
                    "resolution": a["resolution"].strip() or None, "evidence": a["evidence"]}
                   for a in agenda if a["title"].strip() or a["discussion"].strip()],
        "action_items": action_items,   # งานแก้ที่แท็บ ⑤ — ที่นี่ส่งต่อตามที่บันทึกไว้
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


@st.dialog("ยกเลิกการอนุมัติเพื่อแก้รายงาน")
def reopen_dialog(user: dict, mid: int):
    st.warning("รายงานจะกลับเป็นฉบับร่าง (PDF จะมีลายน้ำ “ฉบับร่าง”) และต้องอนุมัติใหม่หลังแก้ — "
               "นัดที่ส่งเข้า Google Calendar ไปแล้วจะถูกลบออกด้วย และส่งใหม่ตอนอนุมัติครั้งถัดไป")
    failed = st.session_state.get(f"reopen_failed_{mid}")
    if failed:
        st.error("ลบนัดออกจาก Google Calendar ไม่สำเร็จ จึงยังไม่ยกเลิกการอนุมัติ:")
        for f in failed:
            st.markdown(f"- {common.md_escape(f['description'])} — {f['error']}")
    force = st.checkbox("ยกเลิกการอนุมัติต่อไป แม้นัดข้างต้นจะค้างใน Calendar (ฉันจะลบเอง)", key=f"reopen_force_{mid}") if failed else False
    if st.button("ยกเลิกการอนุมัติ", type="primary"):
        try:
            out = service.reopen_report(user, mid, force=force)
        except ServiceError as e:
            if e.kind == "calendar_failed":
                st.session_state[f"reopen_failed_{mid}"] = e.extra["failed"]
                st.rerun(scope="fragment")
            st.error(str(e))
            return
        st.session_state.pop(f"reopen_failed_{mid}", None)
        _bump(mid)
        msg = "รายงานกลับเป็นฉบับร่างแล้ว — แก้ไขได้"
        if out["deleted"]:
            msg += f" (ลบนัดออกจาก Google Calendar {out['deleted']} รายการ)"
        common.flash("info", msg)
        if out["failed"]:
            common.flash("warning", "นัดที่ยังค้างอยู่ใน Google Calendar (ต้องลบเอง): " + ", ".join(f["description"] for f in out["failed"]))
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

    _history_panel(user, mid, editable)

    st.divider()
    unsaved = editable and has_unsaved(content, summary, other, agenda)
    st.session_state[f"report_unsaved_{mid}"] = unsaved      # แท็บ ⑤ อ่านค่านี้เพื่อไม่ให้อนุมัติทั้งที่ยังมีของค้างไม่ได้บันทึก
    if unsaved:
        st.warning("มีการแก้ไขที่ยังไม่ได้บันทึก — กด 💾 บันทึกร่าง ก่อนไปขั้นอนุมัติ")
    b1, b2, b3, b4 = st.columns([1.3, 1.5, 1.4, 1.5])
    if editable:
        if b1.button("💾 บันทึกร่าง", key=f"savedraft_{mid}"):
            try:
                service.save_report_draft(user, mid, _collect(mid, summary, other, agenda, content["action_items"]))
            except ServiceError as e:
                st.error(str(e))
            else:
                _bump(mid)
                common.flash("success", "บันทึกร่างแล้ว")
                st.rerun()
        if b2.button("✨ ให้ AI ร่างใหม่", key=f"regen_{mid}"):
            _regenerate_dialog(user, mid)
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
            reopen_dialog(user, mid)
    if not approved:        # PDF ฉบับเต็มหลังอนุมัติดาวน์โหลดที่แท็บ ⑤
        try:
            pdf, name = service.build_pdf(user, mid)
            b4.download_button("⬇ ดาวน์โหลด PDF (ฉบับร่าง)", data=pdf, file_name=name,
                               mime="application/pdf", key=f"pdf_{mid}")
        except ServiceError as e:
            b4.caption(str(e))

