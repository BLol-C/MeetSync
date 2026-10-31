"""
MeetSync — หน้าเว็บ (Streamlit)

รัน:  venv\\Scripts\\streamlit run streamlit_app.py   (หรือใช้ run.ps1 เปิดทั้งหน้าเว็บและบริการบอทพร้อมกัน)
เปิด: http://localhost:8501

หน้าเว็บทำงานกับฐานข้อมูลผ่าน service.py และคุมบอทผ่านบริการบอท (app.py) ด้วย botclient.py
"""

import streamlit as st

st.set_page_config(page_title="MeetSync", page_icon="🎙️", layout="wide", initial_sidebar_state="expanded")

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from ui import auth, common, home, meeting, new_meeting  # noqa: E402


def main():
    common.inject_css()
    auth.ensure_db()
    user = auth.require_login()
    common.render_sidebar(user, dev=auth.is_dev())
    common.show_flash()

    meeting_id = st.query_params.get("m")
    if meeting_id and meeting_id.isdigit():
        meeting.render(user, int(meeting_id))
    elif st.query_params.get("view") == "new":
        new_meeting.render(user)
    else:
        home.render(user)


main()
