# แผนที่โค้ดหลังบ้าน เรียงตามฟังก์ชันใน TOR (ไฟล์ `TOR\v2_01418499_TOR_MeetSync.docx`)

ใช้ไล่อธิบายอาจารย์ทีละฟังก์ชัน เลขหลังชื่อไฟล์คือเลขบรรทัด ณ commit `088fd61` (เลื่อนได้ถ้าแก้โค้ด ตรวจอีกรอบก่อนวันตรวจ)
ภาพรวมโครงสร้าง: หน้าเว็บ Streamlit (`streamlit_bot_service.py`, โฟลเดอร์ `ui/`) → ชั้นบริการ `service.py` → ฐานข้อมูล `db.py`
และบริการบอทแยกโปรเซส (FastAPI ใน `bot_service.py`) ควบคุมเบราว์เซอร์ผ่าน `bot/meet_engine.py` หน้าเว็บคุยกับบริการบอทผ่าน HTTP (`bot/botclient.py`)

## ฟังก์ชัน 1: เชื่อมต่อห้องประชุมและดักจับคำบรรยายสด

| ข้อใน TOR | โค้ด | อธิบายสั้นๆ |
|---|---|---|
| Host นำเข้าลิงก์ Meet ผ่านหน้าเว็บ | `ui/new_meeting.py:133` (ช่องลิงก์), `service.py:60` (ตรวจรูปแบบลิงก์), `service.py:89` `create_meeting` | รับลิงก์ ตรวจรูปแบบ แล้วบันทึกการประชุม |
| สั่งบอทเริ่มทำงาน | `ui/tab_bot.py:12,146` (ปุ่มเริ่มบอท) → `bot/botclient.py:100` `start` → `bot_service.py:278` `/bot/start` | หน้าเว็บเรียกบริการบอทผ่าน HTTP ด้วยโทเคนร่วม |
| ใช้ Playwright เข้าห้อง | `bot/meet_engine.py:601` `_run` (เปิด Chrome แบบ persistent profile ที่ :611), `:654` ปุ่มเข้าร่วม (`JOIN_BUTTON_RE` :272) | ใช้โปรไฟล์ Chrome ที่ล็อกอินไว้ รอผู้จัดอนุญาตเข้าห้อง |
| เปิด Closed Captions อัตโนมัติ | `bot/meet_engine.py:401` `_enable_captions`, `:424` `_ensure_captions` | ตรวจยืนยันจากหน้าจอก่อนรายงานว่าเปิดสำเร็จ |
| ตรวจสถานะและออกจากห้องเมื่อประชุมจบ | `bot/meet_engine.py:501` `_monitor` (ระบบเฝ้าระวัง `WATCHDOG_INTERVAL_S` :278 = 15 วินาที), `:319` `stop`, `bot_service.py:315` `/bot/stop` | ตรวจว่าหน้า ตัวจับคำบรรยาย และกรอบคำบรรยายยังอยู่ ถ้าหลุดจะปิดการบันทึกอย่างเรียบร้อย |

## ฟังก์ชัน 2: ดักจับและจัดระเบียบข้อความคำบรรยายสด

| ข้อใน TOR | โค้ด | อธิบายสั้นๆ |
|---|---|---|
| ดักจับข้อความและชื่อผู้พูดแบบเรียลไทม์ | `bot/meet_engine.py:140-201` (JavaScript ฝังในหน้า Meet ใช้ `MutationObserver` :201), `:461` `_install_watcher`, `:341` `_emit` | อ่านกรอบคำบรรยายจากหน้าเว็บ ส่งเป็นเหตุการณ์ให้บริการบอท |
| รับเหตุการณ์และบันทึกลง MySQL | `bot_service.py:162` `_handle_event`, `:148` `_save_worker_loop` (คิวเขียนตัวเดียว), `db.py:976` `insert_segment` | บันทึกทุกประโยคที่จบ ข้อความยาวเกิน 3,000 ตัวอักษรแบ่งเป็นหลาย segment (`bot_service.py:35,94-124`) ไม่เก็บไฟล์เสียง |
| อ่านรายชื่อผู้เข้าร่วมในห้อง | `bot/meet_engine.py:224-274` (อ่านแผง "บุคคล"), `:470` `_scan_participants`, `:492` `_final_scan` | อ่านทันทีที่เข้าห้อง ทุก 60 วินาที และตอนหยุดบอท |
| ตั้งสถานะ "เข้าร่วม" ให้ผู้ที่ชื่อตรง | `bot_service.py:139` `_persist_participants` → `db.py:655` `mark_present_by_names` (ตั้ง `seen_in_room` :665), `db.py:940` `rematch_meet_speakers` | จับคู่ด้วย "ชื่อภาษาอังกฤษ" (meet_alias) ที่ลงทะเบียนไว้ ไม่เดาชื่อ |

**ส่วนที่อาจารย์ขอดูโดยเฉพาะ (ตรวจจับผู้เข้าร่วมจาก Meet):** `bot/meet_engine.py:224-274` และ `:470-500` กับ `db.py:655` ตามลำดับ

## ฟังก์ชัน 3: ตรวจทานและยืนยันข้อความถอดเสียง

| ข้อใน TOR | โค้ด | อธิบายสั้นๆ |
|---|---|---|
| แก้ข้อความ เปลี่ยนผู้พูด ลบช่วง | `ui/tab_transcript.py:189` → `service.py:388` `apply_segments_table` | เก็บข้อความต้นฉบับไว้ใน `original_text` |
| รวมชื่อที่ Meet แสดงให้ตรงรายชื่อ | `ui/tab_transcript.py:91,118` → `service.merge_people` → `db.py:933` | รวมชื่อซ้ำ/ชื่อนอกรายชื่อ |
| ยืนยันผู้เข้าร่วมให้ครบ (มา/ไม่มา/จับคู่/นอกรายชื่อ) | `ui/tab_transcript.py:54` `_confirm_section`, `db.py:875` `exclude_meet_speaker` | ระบบไม่บันทึก "ไม่มา" ให้อัตโนมัติ ต้องผู้ใช้ตอบ |
| ยืนยัน transcript ก่อนให้ AI | `ui/tab_transcript.py:205` → `service.py:421` `verify_transcript` | ถ้ายังตอบไม่ครบหรือยังไม่ยืนยัน ระบบไม่ให้ AI สร้างรายงาน |

## ฟังก์ชัน 4: AI สรุปเนื้อหาและสกัดภาระงาน

| ข้อใน TOR | โค้ด | อธิบายสั้นๆ |
|---|---|---|
| วิเคราะห์ transcript ด้วย LLM | `service.py:548` `generate_report` → `reports/summarizer.py` `generate_for_meeting`/`generate_minutes_content` (เรียก Gemini ที่ ~:383-390) | ประชุมยาวเกิน 60,000 ตัวอักษรแบ่งช่วงสกัดก่อน (map-reduce) |
| สกัดภาระงาน (รายละเอียด ผู้รับผิดชอบ กำหนดส่ง) | `reports/summarizer.py:57-79` (สคีมา `AgendaItemAI`, `ActionItemAI`, `MinutesBodyAI`) และ `prompts/minutes_v7.md` | ส่งวันที่ประชุมให้ AI แปลงคำสัมพัทธ์เป็นวันที่จริง |
| โครงสร้าง JSON Schema พร้อมใช้งานต่อ | `reports/summarizer.py:385` (บังคับ `response_schema`), `:403-411` `_parse_body` (ตรวจ), `validate_minutes` (~:178) | สคีมา pydantic ส่งให้ Gemini บังคับรูปแบบ แล้วตรวจซ้ำและเช็คว่าข้อความอ้างอิงมีใน transcript จริง |
| (เสริม) วาระ 2, 3, 4.1 จากการประชุมครั้งก่อน | `reports/continuity.py` `prefill_items`, `apply_carried` | ระบบสร้างโครงจากฐานข้อมูล AI เติมเฉพาะที่มีหลักฐาน |

## ฟังก์ชัน 5: แสดงผลสรุปสำหรับตรวจสอบ (Summary Panel)

| ข้อใน TOR | โค้ด | อธิบายสั้นๆ |
|---|---|---|
| แสดงสรุปและตารางภาระงานบนหน้าเว็บ | `ui/tab_report.py:361` `render`, `:239` `_agenda_cards`, `ui/tab_calendar.py:166` | แสดงคำเตือนและหลักฐานที่ตรวจแล้ว |
| บันทึกลง MySQL ทันทีหลัง AI เสร็จ และแก้ได้ทุกช่อง | `db.py:1367` `save_report`, `:1403` `update_report_content` | ฉบับร่างแก้ได้ ฉบับอนุมัติแล้วล็อก |
| เก็บทุกฉบับเป็นประวัติแบบเพิ่มอย่างเดียว | `db.py:1218` `_record_version` (ตาราง `summary_versions`), `service.py:517` `report_history`, `:534` `use_report_version`, `ui/tab_report.py:178` `_history_panel`, `reports/history.py:108` `diff_reports` | ทุกการ AI ร่าง/แก้/อนุมัติ/ยกเลิกอนุมัติได้ร่างฉบับใหม่ ไม่เขียนทับ เปรียบเทียบและเลือกฉบับเก่าได้ |

## ฟังก์ชัน 6: ส่งออกและแจ้งเตือนภาระงานภายนอก

| ข้อใน TOR | โค้ด | อธิบายสั้นๆ |
|---|---|---|
| อนุมัติแล้วส่งภาระงานเข้า Calendar พร้อมกัน | `ui/tab_calendar.py:137` `_approve_dialog` → `service.py:751` `approve_and_send` → `:593` `approve_report`, `:690` `sync_calendar`, `db.py:1447` `approve_report` | อนุมัติ + ส่งนัดครั้งเดียว ส่งไม่สำเร็จกดส่งซ้ำได้ |
| สิทธิ์ OAuth 2.0 และสร้างนัดอัตโนมัติ | `integrations/calendar_auth.py:48,58,76` (สิทธิ์ `calendar.events`, token เก็บในตาราง users), `integrations/calendar_sync.py:108` `create_event`, `:35` `build_event_body` | นัดเข้าปฏิทินของผู้กดอนุมัติ |
| เก็บประวัติการประชุมให้เปิดดูย้อนหลัง | `ui/home.py:57` `render`, `db.py:587` `list_meetings`, `service.py:236` `delete_meeting` | แสดงผู้สร้างและผู้อนุมัติ ลบการประชุมได้ (อนุมัติแล้วลบนัดด้วย) |
| ช่วงเวลา/หลายวัน (เวลาเริ่ม-สิ้นสุด วันที่สิ้นสุด) | `integrations/calendar_sync.py:35` `build_event_body`, `action_items.due_date_end` | นัดเต็มวัน หลายวัน หรือช่วงเวลา |
| ตรวจนัดที่ส่งไปแล้วและปรับงานตามเมื่อผู้ใช้ยืนยัน | `service.py:711` `check_calendar_changes`, `:738` `apply_calendar_changes`, `integrations/calendar_sync.py:131,150,162` (`event_schedule`, `fetch_event`, `compare_with_google`), `db.py:1526` | ตรวจเมื่อเปิดหน้าหรือกดปุ่ม ไม่ใช่เรียลไทม์ ไม่ซิงก์ผู้รับผิดชอบ |
| ส่งออกรายงานเป็น PDF (หัวข้อตัวหนา เนื้อความปกติ) | `service.py:664` `build_pdf` → `reports/pdf_report.py:192` `build_minutes_pdf` | ฟอนต์ TH Sarabun New ตัดคำไทยไม่ให้ขาดกลางคำ |

หมายเหตุ: แผนที่นี้ผมตรวจชื่อฟังก์ชันและเลขบรรทัดจากโค้ดโดยตรง ไม่ได้อธิบายการทำงานลึกทุกบรรทัด และไม่ได้ยืนยันบางข้อความใน TOR ที่อยู่นอกตาราง (เช่นข้อความเกี่ยวกับกลุ่มผู้ใช้) ตรวจเอง/เปิดโค้ดอ่านประกอบก่อนอธิบาย
