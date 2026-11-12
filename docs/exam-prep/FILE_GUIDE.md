# คู่มือไฟล์ในโปรเจกต์ MeetSync (ทุกไฟล์ว่าทำหน้าที่อะไร)

เลขบรรทัดในเอกสารนี้ตรวจจากโค้ดจริง ณ commit `8094448` วันที่ 11 พ.ย. 2569 ถ้าแก้โค้ดต่อ เลขบรรทัดจะเลื่อน ให้ค้นด้วยชื่อฟังก์ชันแทน
ส่วนที่เขียนว่า "ผมไม่ได้ตรวจ" แปลว่ายังไม่ได้เปิดอ่านลึกถึงบรรทัดนั้น ไม่ได้แปลว่าไม่มี

ไฟล์คู่กัน: `docs/exam-prep/TOR_MAP.md` (ฟังก์ชันใน TOR ตรงกับโค้ดตรงไหน)

---

## 1. ภาพรวมการทำงาน

ระบบมี **สองโปรเซสที่รันแยกกัน** กับ **ฐานข้อมูล MySQL หนึ่งก้อน** (เปิดทั้งคู่ด้วย `.\run` ซึ่งคือ `run.ps1`)

```
ผู้ใช้ ──► Streamlit  (พอร์ต 8501: streamlit_app.py + ui/*)
              │  เรียกฟังก์ชันใน service.py (กฎสิทธิ์/ลำดับขั้นอยู่ที่นี่ที่เดียว)
              ├──► db.py ──► MySQL (8 ตาราง)
              ├──► reports/* (ให้ Gemini ร่างรายงาน, สร้าง PDF)
              ├──► integrations/* (Google Calendar)
              └──► bot/botclient.py ──HTTP+โทเคนลับ──► บริการบอท FastAPI (พอร์ต 8000: bot_service.py)
                                                          └──► bot/meet_engine.py (Playwright เปิด Chrome เข้า Google Meet)
                                                                 └─ อ่านคำบรรยายสด/รายชื่อคนในห้อง ──► db.py (เขียนลง MySQL)
```

ลำดับงานของผู้ใช้ตรงกับแท็บ ①–⑤ ในหน้าการประชุม: ① ข้อมูลและผู้เข้าร่วม → ② บอท → ③ Transcript → ④ รายงาน (AI ร่าง ตรวจ แก้) → ⑤ Calendar (ตรวจงาน อนุมัติ ส่งนัด)
สถานะการประชุมใน DB เดินตามนี้: `scheduled → recording → transcript_review → transcript_verified → draft → approved` (กำหนดที่ `db.py:161-169`)

หลักการแยกชั้น: **หน้าเว็บ (`ui/`) เรียก `service.py` เป็นหลัก** (ยกเว้น `ui/auth.py` ที่เรียก `db.init_schema` และ `db.upsert_user` ตอนเตรียมฐานข้อมูลและบันทึกผู้ใช้ที่ล็อกอิน) ส่วน `service.py` เรียก `db.py`, `reports/`, `integrations/`, `bot/botclient.py`
ถ้าจะไล่โค้ดตามปุ่มบนหน้าจอ ให้เริ่มที่ไฟล์ `ui/tab_*.py` แล้วตามไปที่ `service.<ชื่อฟังก์ชัน>` ตามด้วย `db.<ชื่อฟังก์ชัน>`

---

## 2. ตารางสรุปไฟล์ทั้งหมดที่อยู่ใน git (69 ไฟล์)

| ไฟล์ | บรรทัด | หน้าที่ (สั้น) |
|---|---|---|
| `streamlit_app.py` | 37 | จุดเข้าของหน้าเว็บ: ตรวจล็อกอิน แล้วเลือกหน้า (รายการ / สร้างใหม่ / การประชุม) จาก query string |
| `bot_service.py` | 366 | บริการบอท (FastAPI): สั่งเริ่ม/หยุดบอท รับคำบรรยายสดจากเอนจิน เขียนลง MySQL ผ่านคิวตัวเดียว เป็นที่รับ callback OAuth ของ Calendar |
| `service.py` | 754 | ชั้นบริการ: ตรวจสิทธิ์ ลำดับขั้น และ use case ทั้งหมดที่หน้าเว็บเรียก |
| `db.py` | 1580 | ฐานข้อมูล: สร้างตาราง การเชื่อมต่อ และทุกคำสั่ง SQL |
| `run.ps1` | 23 | เปิดบริการบอทและหน้าเว็บพร้อมกัน ปิดด้วย Ctrl+C |
| `requirements.txt` | 86 | รายการไลบรารี Python ที่ติดตั้ง |
| `README.md` | 162 | คู่มือติดตั้ง/รัน/ทดสอบ/ข้อจำกัด (บางส่วนล้าสมัย ดูหัวข้อ 6) |
| `.gitignore` | 13 | รายการที่ห้ามขึ้น git (ความลับ ข้อมูลประชุมจริง venv) |
| `.streamlit/config.toml` | 11 | ตั้งค่า Streamlit (พอร์ต 8501, ปิดสถิติการใช้, ซ่อนรายละเอียด error) |
| `bot/__init__.py` | 0 | ไฟล์ว่าง ทำให้โฟลเดอร์เป็นแพ็กเกจ |
| `bot/botclient.py` | 121 | ไคลเอนต์ที่หน้าเว็บใช้เรียกบริการบอท และจัดการโทเคนลับ |
| `bot/meet_engine.py` | 711 | เครื่องยนต์บอท: Playwright เข้าห้อง เปิดคำบรรยาย ดักจับข้อความ อ่านรายชื่อ เฝ้าสุขภาพ |
| `integrations/__init__.py` | 0 | ไฟล์ว่าง |
| `integrations/calendar_auth.py` | 89 | OAuth ของ Google Calendar (ขอสิทธิ์ แลก token ต่ออายุ) |
| `integrations/calendar_sync.py` | 189 | สร้าง/อ่าน/เทียบ/ลบนัดใน Google Calendar |
| `reports/__init__.py` | 0 | ไฟล์ว่าง |
| `reports/summarizer.py` | 542 | ให้ Gemini ร่างรายงาน + ชั้นตรวจผล (หลักฐาน ผู้รับผิดชอบ วันที่) |
| `reports/continuity.py` | 137 | สร้างโครงวาระ 2, 3, 4.1 จากรายงานครั้งก่อน และรับสิ่งที่ AI เติมกลับ |
| `reports/history.py` | 157 | เทียบรายงานสองรุ่น (ประวัติแก้ไข) |
| `reports/pdf_report.py` | 433 | วาดไฟล์ PDF รายงานตามแบบฟอร์ม |
| `reports/report_data.py` | 114 | ประกอบส่วนหัวรายงานจากข้อมูลในระบบ (ไม่ผ่าน AI) และวันที่แบบไทย |
| `reports/report_template.py` | 72 | ข้อความและลำดับส่วนของแบบฟอร์ม (แก้ฟอร์มที่ไฟล์นี้) |
| `reports/thai_text.py` | 58 | ตัดคำไทยให้ PDF ขึ้นบรรทัดใหม่ที่รอยต่อคำ |
| `ui/__init__.py` | 0 | ไฟล์ว่าง |
| `ui/auth.py` | 99 | ล็อกอิน Google (OIDC ของ Streamlit) และเตรียมฐานข้อมูลตอนเริ่ม |
| `ui/common.py` | 208 | ของกลางของหน้าเว็บ: ป้ายภาษาไทย แถบข้าง ข้อความแจ้งผล ตัวช่วยจัดหน้า |
| `ui/home.py` | 85 | หน้าแรก รายการการประชุมของฉัน + ลบการประชุม |
| `ui/meeting.py` | 81 | หน้าการประชุม หัวเรื่อง สถานะ และ 5 แท็บ |
| `ui/new_meeting.py` | 154 | หน้าสร้างการประชุมใหม่ |
| `ui/tab_setup.py` | 122 | แท็บ ① ข้อมูลการประชุมและตารางผู้เข้าร่วม |
| `ui/tab_bot.py` | 157 | แท็บ ② เริ่ม/หยุดบอท คำบรรยายสด |
| `ui/tab_transcript.py` | 237 | แท็บ ③ ตรวจ/แก้ transcript ยืนยันผู้เข้าร่วม ยืนยัน transcript |
| `ui/tab_report.py` | 463 | แท็บ ④ ให้ AI ร่าง ตรวจ แก้ วาระ/สรุป ประวัติรุ่น |
| `ui/tab_calendar.py` | 261 | แท็บ ⑤ ตรวจงาน อนุมัติ ส่ง Calendar ตรวจการเปลี่ยนแปลงกลับ |
| `prompts/minutes_v1.md` – `v7.md` | 29–60 | คำสั่ง (prompt) ที่ให้ Gemini ร่างรายงาน เวอร์ชัน 1–7 (ใช้อยู่คือ v7) |
| `prompts/minutes_map_v1.md`, `v2.md` | 14–15 | prompt สกัดบันทึกย่อทีละช่วงสำหรับประชุมยาว (ใช้อยู่คือ v2) |
| `fonts/THSarabunNew.ttf`, `-Bold.ttf`, `README.txt` | – | ฟอนต์ไทยที่ใช้วาด PDF และคำอธิบายที่มา |
| `tools/make_streamlit_secrets.py` | 56 | สร้าง `.streamlit/secrets.toml` สำหรับล็อกอิน Google จาก `.env` |
| `tools/proc_monitor.ps1` | 50 | วัดหน่วยความจำของ Python และ Chrome ของบอททุก N วินาทีเป็น CSV (ใช้วัด soak 1 ชม.) |
| `tools/stability_check.py` | 195 | สร้างบทพูด 90 ประโยค และเทียบบทกับ transcript หลังทดสอบ 1 ชม. |
| `tools/stability_script.txt` | 95 | บทพูดที่สร้างไว้ (ผลของ `stability_check.py make`) |
| `tools/soak_sim.py` | 272 | จำลอง Google Meet ใน Chromium เพื่อทดสอบตัวจับคำบรรยายและ watchdog |
| `tools/ui_smoke.py` | 337 | ขับหน้าเว็บด้วยเบราว์เซอร์จริงตลอดสายงาน (บอท/AI ปลอม) พร้อมเก็บภาพ |
| `tools/review_sheet.py` | 142 | สร้างใบตรวจคุณภาพรายงานที่ AI ร่าง |
| `tools/upgrade_calendar_link.py` | 73 | อัปเกรดฐานข้อมูลเก่าให้มีคอลัมน์ใหม่ (สคริปต์ย้อนยุค) |
| `tools/meet_people_probe.js` | 41 | โค้ดวางใน DevTools เพื่อดูโครงสร้างแผงรายชื่อของ Meet (ใช้ปรับ selector) |
| `tests/` (15 ไฟล์) | – | เทสต์อัตโนมัติ 311 ข้อ |
| `docs/exam-prep/CODE_MAP_TOR.md` | 65 | แผนที่ TOR ฉบับเดิม (แทนด้วย `docs/exam-prep/TOR_MAP.md`) |

(ไม่ได้ยัดไฟล์ฟอนต์ 2 ไฟล์ในตารางเป็นบรรทัดแยกเพราะเป็นไฟล์ข้อมูลไม่ใช่โค้ด)

---

## 3. รายละเอียดทีละไฟล์

### 3.1 ไฟล์ระดับบนสุด

**`streamlit_app.py`** (37 บรรทัด)
- จุดเริ่มของหน้าเว็บ รัน `streamlit run streamlit_app.py`
- `main()` (บรรทัด 21) ตรวจล็อกอินด้วย `ui.auth.require_login` แล้วอ่าน query string: `?m=<id>` เปิดหน้าการประชุม, `?view=new` เปิดหน้าสร้างใหม่, นอกนั้นหน้าแรก (บรรทัด 28-35)

**`bot_service.py`** (366 บรรทัด) บริการบอท FastAPI รันแยกโปรเซสโดยตั้งใจ ให้บอทอยู่ในห้องได้แม้หน้าเว็บรีโหลดหรือปิด
- `_lifespan` (71): ตอนเปิดบริการเรียก `db.init_schema` (ต่อฐานไม่ได้ก็ยังเปิดบริการได้ หน้าเว็บเห็นจาก `/health`) ตอนปิดบริการเรียก `_finalize`
- `_cut_point` (92) / `_SEGMENT_MAX_CHARS = 3000` (35): ตัดข้อความยาวเป็นหลาย segment ที่ช่องว่าง
- `_persist_segment` (99): เขียน segment ลง MySQL (รันใน thread เพราะ PyMySQL เป็นแบบ sync) แถวคำบรรยายเดียวกันถ้าข้อความยาวขึ้นจะ update แถวเดิมแทน insert ใหม่
- `_persist_participants` (139): รับรายชื่อคนในห้องแล้วตั้งสถานะ "เข้าร่วม" (เรียก `db.record_room_names`)
- `_save_worker_loop` (148): คิวเขียนตัวเดียว ให้ลำดับการเขียนถูกต้อง
- `_handle_event` (162) / `_on_engine_event` (201): รับ event จากเอนจิน (คำบรรยาย รายชื่อ สถานะ) เก็บไว้ให้หน้าเว็บอ่านสดและส่งที่ final เข้าคิว
- `_finalize` (211): หยุดบอท รอเขียนที่ค้างให้ครบ แล้วปิดประชุมใน DB เรียกซ้ำได้
- `identity` (242), `_manager_of` (260): ตรวจโทเคนลับและสิทธิ์ผู้ใช้ต่อการประชุมทุกคำขอ
- ปลายทาง HTTP: `GET /health` `health` (274), `POST /bot/start` `bot_start` (279), `POST /bot/stop` `bot_stop` (316), `GET /bot/state` `bot_state` (324), `GET /calendar/connect` `calendar_connect` (349), `GET /auth/google/callback` `calendar_callback` (358) กับ `_safe_return` (343) ที่กัน open redirect
- ตัวแปรที่อ่าน: `BOT_LIVE_ROWS_MAX` (เพดานแถวกล่องสด) ผ่าน `_env_int` (37)

**`service.py`** (754 บรรทัด) กฎสิทธิ์และลำดับขั้นทั้งหมด หน้าเว็บเรียกที่นี่ ทุกฟังก์ชันรับ `user = {user_id, email, name}` ยก `ServiceError` (23) พร้อมข้อความภาษาไทยเมื่อผิดเงื่อนไข
- สิทธิ์: `meeting_for` (36) ให้เฉพาะเจ้าของ หรือประธาน/เลขาที่อีเมลตรง, `_require_status` (46)
- สร้าง/จัดการประชุม: `check_meet_url` (55), `previous_meeting_choices` (69), `create_meeting` (89), `suggest_meeting_number` (152 เตือนชื่อซ้ำและเสนอเลขครั้งที่ถัดไป), `meetings_with_url` (171), `roster_locked` (188 ล็อกตารางรายชื่อระหว่างบอทบันทึก), `update_meeting` (213), `delete_meeting` (236 ลบนัดใน Calendar ด้วยถ้าอนุมัติแล้ว), `following_meetings` (228 ใช้เตือนก่อนลบ)
- ผู้เข้าร่วม: `add_person` (267), `set_person_attendance` (279), `confirm_guest` (291), `exclude_person` (298), `merge_people` (306), `apply_people_table` (320)
- Transcript: `apply_segments_table` (388), `verify_transcript` (421 ปลดล็อกให้ AI ร่างได้), `reopen_transcript` (440), `group_turns` (450 รวมประโยคต่อเนื่องของคนเดียวกันเป็นช่วงพูด), `transcript_text` (478 ไฟล์ .txt)
- รายงาน: `get_report_view` (495), `report_history` (517), `report_version` (524), `use_report_version` (534), `generate_report` (548 เรียก AI), `keep_existing_report` (562), `save_report_draft` (573), `approve_report` (593), `reopen_report` (611), `build_pdf` (664)
- Calendar: `sync_calendar` (690), `check_calendar_changes` (711), `apply_calendar_changes` (738), `approve_and_send` (751 อนุมัติแล้วส่งทันที), `_delete_calendar_events` (626), `invites_sent` (685)

**`db.py`** (1580 บรรทัด) ชั้นข้อมูล เอกสารที่หัวไฟล์ (บรรทัด 1-35) อธิบายแต่ละตาราง
- โครงสร้าง: `_SCHEMA` (บรรทัด 37-160 สร้าง 8 ตาราง: users, meetings, speakers, transcript_segments, summaries, agenda_items, action_items, summary_versions), `init_schema` (239), `_check_schema_is_current` (290), `get_connection` (201), `_tx` (217 ทรานแซกชัน)
- การย้ายโครงสร้างแบบเพิ่มอย่างเดียว (เปิดแอปครั้งแรกหลังอัปเดตโค้ด): `_drop_ai_snapshot_column` (1158), `_move_other_matters_to_agenda` (1171 ย้ายข้อความ "เรื่องอื่น ๆ" เข้า agenda_items หมวด other), `_ensure_task_end_date_column` (1193), `_ensure_invite_sent_column` (1201), `_ensure_pointer_column` (1210), `_backfill_versions` (1248)
- ผู้ใช้/Calendar token: `upsert_user` (323), `save_calendar_token` (343), `clear_calendar_token` (352), `get_calendar_token` (362) token เก็บในคอลัมน์ `users.calendar_token` (LONGTEXT ไม่เข้ารหัส)
- การประชุม: `create_meeting_setup` (383), `begin_recording` (476), `end_meeting` (495), `set_meeting_status` (509 ตรวจการเปลี่ยนสถานะตาม `_STATUS_TRANSITIONS`), `is_meeting_manager` (565), `list_meetings` (587), `delete_meeting` (454)
- ผู้เข้าร่วม/ชื่อ: `normalize_name` (621), `mark_present_by_names` (655), `record_room_names` (675), `_match_key` (702) และ `_find_speaker_for_name` (708) จับคู่เฉพาะ "ชื่อภาษาอังกฤษ" ไม่เดา, `get_or_create_speaker` (723), `add_speaker` (753), `mark_unconfirmed_absent` (786), `merge_speakers` (911), `rematch_meet_speakers` (940), `exclude_meet_speaker` (875), `confirm_meet_speaker_as_guest` (863), `list_unresolved` (892)
- Transcript: `insert_segment` (976), `update_segment` (991), `get_transcript` (1004), `list_segments` (1022), `edit_segment` (1058 เก็บ `original_text` ไว้)
- รายงานและประวัติ: `_record_version` (1218 บันทึกเป็นฉบับต่อท้ายเสมอ ไม่เขียนทับ), `save_report` (1367), `update_report_content` (1403), `use_report_version` (1421), `approve_report` (1447), `reopen_report` (1470), `list_report_versions` (1290), `get_report_version` (1306), `latest_ai_draft` (1271)
- งานและ Calendar: `get_action_item` (1496), `apply_calendar_changes` (1526), `mark_action_item_synced` (1555), `meeting_invites_sent` (1568)
- ตัวแปรที่อ่านตอน import: `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` (ต้องโหลด `.env` ก่อน import ไฟล์นี้)

**`run.ps1`** (23 บรรทัด) ตรวจ venv, สร้าง `.streamlit/secrets.toml` ให้ถ้ายังไม่มี (เรียก `tools/make_streamlit_secrets.py`), เปิด `uvicorn bot_service:app` พอร์ต 8000 เป็นโปรเซสแยก แล้วเปิด Streamlit พอร์ต 8501 เมื่อปิดจะปิดบริการบอทตามด้วย

**`requirements.txt`** ไลบรารีหลัก: streamlit, fastapi/uvicorn, playwright, PyMySQL, google-genai, google-api-python-client/google-auth-oauthlib, pydantic, fpdf2, uharfbuzz, pythainlp, pandas, Authlib, itsdangerous, python-dotenv, pypdf (ใช้ในเทสต์)

**`.gitignore`** ไม่ให้ขึ้น git: `.env`, `.bot_token`, `.streamlit/secrets.toml`, `.meet_profile/` (โปรไฟล์ Chrome ที่ล็อกอิน Google), `venv/`, `__pycache__/`, `ui_smoke_out/`, `ai_review/`, `*.sql`, `soak_*.csv`

**`.streamlit/config.toml`** `headless = true`, พอร์ต 8501, ที่อยู่ localhost, ปิดสถิติ, `toolbarMode = minimal`, `showErrorDetails = none`

### 3.2 `bot/` บอทเข้าห้องประชุม

**`bot/botclient.py`** (121 บรรทัด) ฝั่งหน้าเว็บของการคุยกับบริการบอท
- `get_token` (25): ใช้ `BOT_API_TOKEN` จาก `.env` ถ้าไม่ตั้งจะสร้างไฟล์ `.bot_token` สุ่ม 64 ตัวอักษรให้อัตโนมัติ
- `calendar_connect_token` (49) / `read_calendar_token` (58) / `calendar_connect_url` (62): เซ็นและอ่านโทเคนสำหรับ flow เชื่อม Calendar ด้วย `itsdangerous`
- `BotError` (68), `_call` (84), `start` (100), `stop` (104), `state` (108), `health` (112): ฟังก์ชันที่ `service.py` และ `ui/` เรียก ผู้ใช้ที่ล็อกอินถูกส่งไปในเฮดเดอร์

**`bot/meet_engine.py`** (711 บรรทัด) หัวใจของฟังก์ชัน 1 และ 2 ใน TOR
- `JS_CAPTION_WATCHER` (29-221): JavaScript ที่ฝังในหน้า Meet ใช้ `MutationObserver` (201) เฝ้ากล่องคำบรรยาย รวมกลไก "จุดยึด" (`ANCHOR`, 86) ที่แก้ปัญหา Meet ตัดข้อความหน้าแล้วแก้คำย้อนหลัง ไม่ให้ข้อความต่อซ้ำ
- `JS_READ_PARTICIPANTS` (228-261): JavaScript อ่านรายชื่อคนจากแผง "บุคคล"
- ค่าคงที่ (272-284): `JOIN_BUTTON_RE` (ปุ่มเข้าร่วม), `PARTICIPANT_SCAN_S` (60 วินาที), `WATCHDOG_INTERVAL_S` (15), `LEFT_ROOM_STRIKES` (3 รอบ = หลุดห้อง), `JOIN_WAIT_S`/`CAPTION_WAIT_S` (1200 วินาที = 20 นาที)
- `MeetCaptionEngine` (296): `start` (307), `stop` (319), `_emit` (341 ส่ง event ออก callback), `_guard` (347 เลิกรอเมื่อมีคำสั่งหยุด)
- การเปิดคำบรรยาย: `_caption_state` (372 ดูสถานะจากหน้าจอจริง), `_enable_captions` (401 กดแล้วตรวจซ้ำ), `_ensure_captions` (424 วนจนยืนยันได้หรือหมดเวลา)
- `_place_window` (443): ย่อหน้าต่าง Chrome เก็บไว้ (ค่าเริ่มต้น `BOT_WINDOW=hidden`) ถ้าปิดหน้าต่างทิ้งบอทหลุดจากห้อง
- `_install_watcher` (461), `_scan_participants` (470), `_final_scan` (492)
- `_monitor` (501): ลูปเฝ้าสุขภาพทุก 15 วินาที ตรวจหน้า ตัวจับ กรอบคำบรรยาย อ่านรายชื่อตามรอบ ถ้าหลุดจะออกด้วย `_EngineLost`
- `_run` (601): ขั้นตอนทั้งหมด เปิด Chrome แบบ persistent profile (611, ใช้ `.meet_profile/` ที่ล็อกอินไว้) เข้าห้อง กดปุ่มเข้าร่วม (654) รอผู้จัดอนุญาต (675) เปิดคำบรรยาย แล้วเข้า `_monitor`

### 3.3 `integrations/` Google Calendar

**`integrations/calendar_auth.py`** (89 บรรทัด)
- `SCOPES` (24) ขอสิทธิ์ `calendar.events` เท่านั้น แยกจากสิทธิ์ล็อกอิน
- `build_auth_url` (48), `exchange_code` (58), `is_connected` (64), `get_credentials` (76 ต่ออายุให้ ถ้า Google ปฏิเสธจะยก `CalendarReconnectNeeded` 68 และล้าง token)
- `REDIRECT_URI` (25) ชี้ `http://localhost:8000/auth/google/callback` ซึ่งเป็นปลายทางใน `bot_service.py`

**`integrations/calendar_sync.py`** (189 บรรทัด)
- `attendee_email` (24): หาอีเมลผู้รับผิดชอบจากรายชื่อผู้เข้าร่วม (ต้องจับคู่ชื่อได้)
- `build_event_body` (35): แปลงงานเป็นเนื้อ event (เต็มวัน หลายวัน หรือช่วงเวลา) เป็นฟังก์ชันล้วน ทดสอบได้ไม่ต่อ Google
- `create_event` (108): สร้างนัด แนบ attendee ถ้ามีอีเมล ส่งอีเมลเชิญจริงเฉพาะเมื่อ `send_invites` (บรรทัด 124)
- `event_schedule` (131), `fetch_event` (150), `compare_with_google` (162): อ่านนัดกลับมาเทียบกับงานในระบบ (same / changed / deleted)
- `delete_event` (180): ลบนัดตอนยกเลิกอนุมัติหรือลบประชุม

### 3.4 `reports/` รายงานและ AI

**`reports/summarizer.py`** (542 บรรทัด) ตัวสร้างรายงานด้วย Gemini + ชั้นตรวจผล
- ค่าตั้ง (28-49): `PROMPT_NAME` (minutes_v7), `MAP_PROMPT_NAME` (minutes_map_v2), `_MODEL` (`GEMINI_MODEL`), `SINGLE_PASS_CHARS` = 60,000 ตัวอักษร, `CHUNK_CHARS` = 30,000, `GROUNDED_THRESHOLD` = 0.8
- สคีมา pydantic ที่บังคับรูปแบบคำตอบ: `AgendaItemAI` (57), `ActionItemAI` (65), `CarriedItemAI` (75), `MinutesBodyAI` (82)
- ตรวจหลักฐาน: `grounding_score` (130) เทียบข้อความอ้างอิงกับ transcript โดยไม่สนช่องว่าง, `find_unclear_number` (104)
- `match_participant_name` (150): จับคู่ชื่อผู้รับผิดชอบที่ AI ให้กับรายชื่อ
- `validate_minutes` (187): ทำความสะอาดและตรวจคุณภาพ ใส่คำเตือน (ผู้รับผิดชอบไม่อยู่ในรายชื่อ วันที่ผิด ไม่มีหลักฐาน)
- `split_chunks` (369) แบ่งประชุมยาว, `_gemini_generate` (386; ใช้ `response_schema` ที่บรรทัด 404 และลองซ้ำเมื่อ Gemini ล้มเหลวชั่วคราว 413), `_parse_body` (422)
- `generate_minutes_content` (435) ตัวหลัก: ประชุมสั้นส่งครั้งเดียว ยาวใช้ map-reduce (459)
- `generate_for_meeting` (517): ดึง transcript สร้างโครงวาระจากครั้งก่อน (`continuity`) ให้ AI ร่าง ผสมกัน แล้วบันทึกลง DB และเลื่อนสถานะเป็น draft

**`reports/continuity.py`** (137 บรรทัด) วาระที่ต้องรู้ข้อมูลครั้งก่อน
- `previous_report` (18), `prefill_items` (81 สร้างโครงวาระ 2, 3, 4.1 จากข้อมูลจริงใน DB), `prompt_list` (105 รายการที่ส่งให้ AI อ้างด้วยรหัส R1, R2), `apply_carried` (118 เติมสิ่งที่ AI ตอบเข้าโครง ข้ามรายการที่ไม่มีรหัสหรือไม่มีหลักฐาน)

**`reports/history.py`** (157 บรรทัด) `text_diff` (22), `diff_reports` (108) เทียบรายงานสองรุ่น หน้าเว็บใช้แสดงส่วนที่ลบ (แดงขีดฆ่า) และเพิ่ม

**`reports/pdf_report.py`** (433 บรรทัด) วาด PDF ด้วย fpdf2: A4 ขอบ 72 pt ฟอนต์ TH Sarabun New
- `_ReportPDF` (41), ตัวช่วยวาด `_para` (117), `_dots` (133), `_fmt_due` (158 วันเดียวหรือช่วงวัน), `_fmt_time` (175)
- `build_minutes_pdf` (192): ฟังก์ชันหลัก ยังไม่อนุมัติใส่ลายน้ำ "ฉบับร่าง" ทุกหน้า

**`reports/report_data.py`** (114 บรรทัด) `build_header` (75) สร้างส่วนหัวรายงาน (ผู้มา ผู้ไม่มา ผู้เข้าร่วม วันเวลา สถานที่) จากข้อมูลในระบบ ไม่ให้ AI สร้าง, `header_warnings` (45), `thai_date` (15) และตัวช่วยวันเวลาแบบไทย

**`reports/report_template.py`** (72 บรรทัด) ข้อความหัวข้อและลำดับส่วนของแบบฟอร์ม (`labels`, `sections`, `agenda`) แก้ฟอร์มที่ไฟล์นี้โดยไม่แตะโค้ดวาด

**`reports/thai_text.py`** (58 บรรทัด) `break_thai` (42) ใช้ pythainlp แทรกจุดตัดคำ, `wrap_chunks` (54) แบ่งข้อความให้ขึ้นบรรทัดใหม่ที่รอยต่อคำ

### 3.5 `ui/` หน้าเว็บ

**`ui/auth.py`** ล็อกอิน: `ensure_db` (35), `current_user` (62), `render_login` (80), `require_login` (94) มีโหมดทดสอบ `MEETSYNC_DEV_USER` (ห้ามใช้ตอนใช้งานจริง)
**`ui/common.py`** ของกลาง: ป้ายวาระ `AGENDA_SECTION_LABEL` (44-50), `render_sidebar` (152 พร้อมสถานะบอทและ Calendar), `go` (105 เปลี่ยนหน้าผ่าน query string), `flash`/`show_flash` (117/121), `md_escape` (76), `previous_meeting_select` (83), `credit_text` (131)
**`ui/home.py`** หน้าแรก `render` (57), `delete_dialog` (21)
**`ui/meeting.py`** หน้าการประชุม `render` (38) วาด 5 แท็บพร้อมกันเพื่อให้สิ่งที่พิมพ์ค้างไม่หายตอนสลับแท็บ, `_bot_info` (14), ปุ่มเปลี่ยนชื่อ (53)
**`ui/new_meeting.py`** `render` (123), ช่องลิงก์ (133), `_create` (54), `_render_title_choice` (74) เตือนชื่อซ้ำและเสนอเลขครั้งที่, `_render_duplicate_choice` (100) เตือนลิงก์ Meet ซ้ำ
**`ui/tab_setup.py`** แท็บ ① `render` (56) เรียก `service.apply_people_table` (115)
**`ui/tab_bot.py`** แท็บ ② `_start` (10) `_stop` (19) `bot_phase` (53) `_live_panel` (80 รีเฟรชกล่องสดทุก 1 วินาที) `render` (123)
**`ui/tab_transcript.py`** แท็บ ③ `_confirm_section` (54 ยืนยันผู้เข้าร่วม: มา/ไม่มา/จับคู่/ชื่อนอกรายชื่อ), `render` (127), บันทึกการแก้ (189), ยืนยัน transcript (209), `_reopen_dialog` (225)
**`ui/tab_report.py`** แท็บ ④ `render` (361), `_generate` (311), `_agenda_cards` (239), `_history_panel` (178), `_show_diff` (127), `_use_dialog` (161), `reopen_dialog` (332), `has_unsaved` (292)
**`ui/tab_calendar.py`** แท็บ ⑤ `render` (166), `_approve_dialog` (137 อนุมัติแล้วส่งทันที), `_calendar_changes` (84 ตรวจการเปลี่ยนแปลงกลับ), `items_df` (32), `status_text` (23)

### 3.6 `prompts/` คำสั่งของ AI

- `minutes_v7.md` (ใช้อยู่ ตั้งใน `summarizer.py:28`): ให้ AI สร้างวาระ 1 และ 4.2 เอง ใส่ `carried` เติมวาระ 2, 3, 4.1 พร้อมหลักฐาน และ `other_matters` พร้อม `other_evidence`
- `minutes_map_v2.md` (ใช้อยู่): ใช้ตอนประชุมเกิน 60,000 ตัวอักษร สกัดบันทึกย่อทีละช่วง
- `minutes_v1.md` – `v6.md`, `minutes_map_v1.md`: เวอร์ชันเก่า ไม่ได้ถูกเลือกใช้โดยค่าเริ่มต้นแล้ว (v5 และ v6 ถูกเรียกในเทสต์ `test_summarizer.py:300,311`) ผมเปิดค้นแล้วว่า v1–v4 และ map_v1 ไม่มีโค้ดอ้างถึง (ยกเว้นชื่อ `minutes_v2`/`minutes_v4` เป็นข้อความตัวอย่างในเทสต์ `test_db.py:374` `test_review_sheet.py:68`)

### 3.7 `tools/` เครื่องมือช่วย (ไม่ใช่ส่วนของระบบที่รันตอนใช้งาน)

- `proc_monitor.ps1`: วัดหน่วยความจำ Python+Chrome ทุก 60 วินาที เก็บเป็น `soak_<เวลา>.csv` (ไม่ขึ้น git) ใช้ `-Summarize` สรุปแนวโน้ม เกณฑ์ที่เสนอคือหน่วยความจำช่วง 1/4 สุดท้ายไม่สูงกว่า 1/4 แรกเกิน ~15%
- `stability_check.py`: `make` สร้างบทพูด 90 ประโยค (`stability_script.txt`) `check <meeting_id>` เทียบบทกับ transcript ใน DB: % ประโยคที่จับได้ ประโยคที่หาย ช่วงเวลา และจำนวนช่วงซ้ำ
- `soak_sim.py`: `soak --minutes N` จำลอง Meet ด้วย DOM ปลอมเทียบข้อความที่จับได้กับที่พูดจริงและวัด JS heap/DOM/listeners, `lifecycle` ทดสอบ watchdog (ประชุมจบ หน้าปิด โหลดใหม่) ไม่ต้องมีห้อง Meet จริงและไม่แตะ DB
- `ui_smoke.py`: ขับหน้าเว็บด้วย Chromium ตลอดสายงานโดยบอทและ AI ปลอม บนฐานข้อมูลชั่วคราว เก็บภาพใน `ui_smoke_out/`
- `review_sheet.py <meeting_id>`: สร้างใบตรวจในโฟลเดอร์ `ai_review/` (ไม่ขึ้น git เพราะมีเนื้อหาประชุมจริง)
- `make_streamlit_secrets.py`: สร้าง `.streamlit/secrets.toml` จาก `GOOGLE_CLIENT_ID/SECRET` ใน `.env` ไม่พิมพ์ค่าลับ ต้องเพิ่ม redirect URI `http://localhost:8501/oauth2callback` ใน Google Cloud Console
- `upgrade_calendar_link.py`: สคริปต์อัปเกรดฐานข้อมูลเก่า (เพิ่ม `google_calendar_link`, `seen_in_room`; ลบ `calendar_synced` และตาราง `calendar_tasks` ถ้าว่าง) เป็นของรุ่นเก่า ฐานข้อมูลที่สร้างใหม่ไม่ต้องรัน
- `meet_people_probe.js`: วางใน DevTools ของแท็บ Meet เพื่อดูโครงสร้างแผงรายชื่อ ใช้ตอนปรับ selector

### 3.8 `tests/` เทสต์อัตโนมัติ 311 ข้อ (รันผ่านทั้งหมดวันที่ 11 พ.ย. 2569 ใช้เวลา ~5.4 นาที)

รัน: `venv\Scripts\python -m unittest discover -s tests -t . -v` (โปรเจกต์ใช้ `unittest` ไม่ใช่ pytest) เทสต์ที่ใช้ MySQL สร้างฐานข้อมูลชั่วคราว `meetsync_test_<สุ่ม>` ให้เองและลบตอนจบ ไม่แตะฐานจริง

| ไฟล์ | ข้อ | ทดสอบอะไร |
|---|---|---|
| `dbcase.py` | – | ตัวช่วยร่วม: สร้าง/ลบฐานข้อมูลชั่วคราวให้เทสต์ที่ใช้ DB |
| `test_db.py` | 35 | ชั้นข้อมูล: โครงสร้าง workflow ผู้พูด segment รายงาน ประวัติ |
| `test_service.py` | 92 | ชั้นบริการครบวงจร: สิทธิ์ ตั้งค่า ผู้เข้าร่วม transcript รายงาน อนุมัติ Calendar PDF ลบ |
| `test_bot_service.py` | 22 | บริการบอทผ่าน HTTP จริงด้วยเอนจินปลอม (โทเคน สถานะ คิวเขียน ตัดข้อความยาว) |
| `test_summarizer.py` | 33 | ชั้นตรวจ AI ไม่เรียก Gemini จริง (validate, หลักฐาน, แบ่งช่วง, prompt, ลองซ้ำ, `carried`) |
| `test_calendar.py` | 23 | สร้าง event (ข้ามเที่ยงคืน เต็มวัน หลายวัน), อีเมลผู้ร่วมนัด, ลิงก์เชื่อม |
| `test_history.py` | 16 | เทียบรายงานสองรุ่น |
| `test_ui.py` | 58 | หน้าเว็บด้วย `AppTest` (ไม่เปิดเบราว์เซอร์) และการแปลงตารางที่แก้เป็นข้อมูล |
| `test_caption_watcher.py` | 4 | ตัวจับคำบรรยาย (JS) กับหน้า Meet จำลองใน Chromium จริง: sliding window ไม่ซ้ำไม่หาย |
| `test_participant_scan.py` | 8 | อ่านรายชื่อคนในห้องกับหน้าจำลอง และจังหวะอ่าน (ทันทีที่เข้า ตอนหยุด) |
| `test_join_button.py` | 3 | ปุ่มเข้าห้อง รวมหน้า "เปลี่ยนที่นี่" |
| `test_stability_check.py` | 5 | เครื่องมือเทียบบทกับ transcript |
| `test_review_sheet.py` | 5 | ใบตรวจคุณภาพ AI |
| `test_thai_text.py` | 7 | ตัดคำไทยใน PDF ไม่ตัดกลางคำ |

### 3.9 `docs/` และ `fonts/`

- `docs/exam-prep/CODE_MAP_TOR.md`: แผนที่เดิม (ใน `docs/exam-prep/TOR_MAP.md` มีฉบับที่ตรวจเลขบรรทัดใหม่ทั้งหมดแทน)
- `docs/SCRIPT_1HR_natural.md`, `docs/SCRIPT_15MIN_CONTINUE_V2.md`: บทพูดทดสอบ (ยังไม่ได้ commit)
- `fonts/`: ฟอนต์ TH Sarabun New (ปกติ/ตัวหนา) แจกฟรีจาก SIPA ใช้ใน `pdf_report.py`

---

## 4. ไฟล์ที่อยู่ในเครื่องแต่ไม่ได้อยู่ใน git

| ไฟล์/โฟลเดอร์ | คืออะไร | ลบได้ไหม |
|---|---|---|
| `.env` | ค่าตั้งและความลับ (DB, Gemini, Google OAuth) | **ห้ามลบ ห้ามส่งต่อ** |
| `.bot_token` | โทเคนลับที่หน้าเว็บกับบริการบอทใช้คุยกัน (สร้างอัตโนมัติ) | ลบได้ สร้างใหม่เองตอนรันครั้งหน้า |
| `.streamlit/secrets.toml` | ค่าล็อกอิน Google ของ Streamlit (สร้างจาก `.env`) | ลบได้ แล้วรัน `tools/make_streamlit_secrets.py` ใหม่ |
| `.meet_profile/` | โปรไฟล์ Chrome ของบอท (ล็อกอิน Google ไว้) | ลบแล้วต้องล็อกอินบัญชีบอทใหม่ |
| `venv/` | สภาพแวดล้อม Python | ลบแล้วต้องติดตั้งใหม่ |
| `__pycache__/` | แคชของ Python | ลบได้ปลอดภัย |
| `ui_smoke_out/` | ภาพหน้าจอจาก `ui_smoke.py` | ลบได้ |
| `ai_review/` | ใบตรวจคุณภาพ AI (มีเนื้อหาประชุมจริง) | ลบได้ถ้าไม่ใช้ |
| `.claude/` | ค่าตั้งของ Claude Code | ไม่เกี่ยวกับระบบ |

---

## 5. ตัวแปรใน `.env` ที่โค้ดอ่าน (ชื่อเท่านั้น ไม่มีค่า)

| ตัวแปร | ใช้ที่ไหน | ความหมาย |
|---|---|---|
| `DB_HOST` `DB_PORT` `DB_USER` `DB_PASSWORD` `DB_NAME` | `db.py` | การเชื่อมต่อ MySQL |
| `GEMINI_API_KEY` | `reports/summarizer.py` | คีย์เรียก Gemini |
| `GEMINI_MODEL` | `reports/summarizer.py:37` | ชื่อโมเดล (ค่าเริ่มต้น `gemini-3.6-flash`) |
| `MINUTES_PROMPT` `MINUTES_MAP_PROMPT` | `summarizer.py:28,35` | ชื่อไฟล์ prompt |
| `MINUTES_SINGLE_PASS_CHARS` `MINUTES_CHUNK_CHARS` | `summarizer.py:44-45` | เกณฑ์ตัวอักษรที่เริ่มแบ่งช่วง และขนาดต่อช่วง |
| `GOOGLE_CLIENT_ID` `GOOGLE_CLIENT_SECRET` | `calendar_auth.py`, `make_streamlit_secrets.py` | OAuth ของ Google |
| `CALENDAR_SEND_INVITES` | `ui/tab_calendar.py:56` | ตั้งเป็น `1` = ช่อง "ส่งอีเมลเชิญ" ในแท็บ ⑤ ติ๊กไว้เป็นค่าเริ่มต้น |
| `BOT_API_TOKEN` | `bot/botclient.py` (`get_token`) | โทเคนลับร่วมของสองโปรเซส (ถ้าไม่ตั้งใช้ไฟล์ `.bot_token`) |
| `BOT_API_URL` `BOT_PUBLIC_URL` `STREAMLIT_URL` | `bot/botclient.py:18-20` | ที่อยู่บริการบอท (ฝั่งหน้าเว็บ) ที่อยู่ที่เบราว์เซอร์เปิดตอนเชื่อม Calendar และที่อยู่หน้าเว็บ |
| `BOT_WINDOW` | `meet_engine.py:22` | `hidden` (ค่าเริ่มต้น ย่อหน้าต่างบอท) หรือ `visible` |
| `BOT_PARTICIPANT_SCAN_S` | `meet_engine.py:273` | ทุกกี่วินาทีที่อ่านรายชื่อ (ค่าเริ่มต้น 60) |
| `BOT_LIVE_ROWS_MAX` | `bot_service.py` | เพดานแถวกล่องคำบรรยายสด (ค่าเริ่มต้น 150) |
| `MEETSYNC_DEV_USER` | `ui/auth.py` | โหมดทดสอบข้ามล็อกอิน **ห้ามตั้งตอนใช้งานจริง** |

---

## 6. ข้อสังเกตที่เจอตอนอ่านโค้ด (ของที่ล้าสมัยหรือไม่ตรงกัน ผมไม่ได้แก้อะไร)

| ที่ไหน | เรื่อง |
|---|---|
| `integrations/calendar_auth.py` บรรทัด 9-10 (คำอธิบายหัวไฟล์) | เขียนว่า token เก็บในตาราง `calendar_tokens` ซึ่งไม่มีตารางนี้แล้ว token อยู่ที่ `users.calendar_token` ล้าสมัย |
| `db.py` คำอธิบาย `init_schema` (239) และ README บรรทัด 103, 152 | บอกว่าไม่มีระบบอัปเกรดโครงสร้างให้ลบฐานแล้วสร้างใหม่ แต่โค้ดมีการย้ายแบบเพิ่มอย่างเดียว (`_ensure_*`, `_move_other_matters_to_agenda`) ที่ทำงานตอนเปิดแอป |
| `README.md` บรรทัด 126 | บอกว่า 150 เทสต์ ณ 9 ต.ค. ปัจจุบันคือ 311 |
| `README.md` บรรทัด 121 | อ้างถึง `docs/ai_quality_check.md` ซึ่งไม่มีไฟล์นี้ใน `docs/` |
| `README.md` บรรทัด 142, 159 | ข้อความเรื่องทดสอบ Meet จริงรอบเดียวและ prompt v7 "ยังไม่ได้ทดสอบกับ Gemini จริง" อาจไม่ตรงกับสถานะล่าสุด (คุณแจ้งว่าทดสอบวาระ 2, 3, 4.1, 5 แล้ว) |
| `docs/exam-prep/CODE_MAP_TOR.md` บรรทัด 4 | ชื่อ `streamlit_bot_service.py` ผิด (ที่ถูกคือ `streamlit_app.py`) เกิดจากการแทนที่ชื่อไฟล์ตอนเปลี่ยน `app.py` เป็น `bot_service.py` |
| `prompts/` | v1–v4 และ `minutes_map_v1` ไม่มีโค้ดเรียกใช้ (เก็บเป็นประวัติ) |
| `tools/upgrade_calendar_link.py` | เป็นสคริปต์ของฐานข้อมูลรุ่นเก่า ฐานข้อมูลใหม่ไม่ต้องใช้ |
| `.meet_profile/Default/...` | โฟลเดอร์ข้อมูลของ Chrome ไม่ใช่โค้ด |

---

## 7. วัด soak 1 ชั่วโมงกับห้อง Meet จริง (ขั้นตอนตาม README และเครื่องมือในโปรเจกต์)

1. เปิดห้อง Meet และเริ่มบอทจากแท็บ ② ให้คนพูดสลับกันตลอด (ห้องฟรีจำกัด 60 นาทีเมื่อมีผู้เข้าร่วม 3 คนขึ้นไป)
2. เปิดอีกหน้าต่าง PowerShell ที่โฟลเดอร์โปรเจกต์: `powershell -ExecutionPolicy Bypass -File tools\proc_monitor.ps1 -Minutes 95` (จะได้ `soak_<เวลา>.csv` ที่ไม่ขึ้น git)
3. ถ้าจะใช้บทพูดมาตรฐาน: `venv\Scripts\python tools\stability_check.py make` ได้ `tools/stability_script.txt` (90 ประโยค ทุก ~40 วินาที)
4. หลังจบ: `powershell -File tools\proc_monitor.ps1 -Summarize soak_<เวลา>.csv` ดูแนวโน้ม (เกณฑ์เสนอ: ช่วง 1/4 สุดท้ายไม่สูงกว่าช่วง 1/4 แรกเกิน ~15%)
5. เทียบการจับข้อความ: `venv\Scripts\python tools\stability_check.py check <meeting_id>`

ผลจำลอง 10 นาทีที่ผมรันวันนี้ (`soak_sim.py soak --minutes 10`): พูด 123 ประโยค จับครบ 0 ผิด 0 หาย JS heap 0.8 → 0.9 MB, DOM node 21 → 20, listener 0 → 0 ผ่าน (ตัวเลขใน Proposal หน้า 67 คือ 150 ประโยค ต่างกันเพราะจำนวนประโยคแต่ละรอบไม่เท่ากัน)
