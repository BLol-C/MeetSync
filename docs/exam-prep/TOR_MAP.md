# แผนที่โค้ดตามฟังก์ชันใน TOR (ไฟล์ `TOR\v2_01418499_TOR_MeetSync.docx`)

ใช้ไล่อธิบายอาจารย์ทีละฟังก์ชันตามลำดับในตาราง TOR และใช้ไล่ "จี้" โค้ดเองว่าแต่ละข้อทำงานจริงตรงไหน
ฉบับนี้แทน `docs/exam-prep/CODE_MAP_TOR.md` (ฉบับเดิมเลขบรรทัดเก่าและมีชื่อไฟล์ผิดหนึ่งที่) ผมไม่ได้ลบฉบับเดิม

- เลขบรรทัดตรวจจากโค้ดจริง ณ commit `8094448` วันที่ 11 พ.ย. 2569 ถ้าแก้โค้ด เลขเลื่อน ให้ค้นด้วยชื่อฟังก์ชัน
- คอลัมน์ "เทสต์" คือชื่อเทสต์ในโฟลเดอร์ `tests/` ที่ผมเห็นชื่อจริงและเปิดชื่อตรงกับเรื่องนั้น (ไม่ได้เปิดอ่านเนื้อเทสต์ทุกข้อ) ชุดเทสต์ทั้งหมด 311 ข้อ รันผ่านทั้งหมดวันที่ 11 พ.ย. 2569
- ที่ไม่ได้ยืนยัน (ผมไม่เห็นหลักฐาน) เขียนกำกับไว้ในคอลัมน์ "หมายเหตุ"
- ไฟล์ไหนทำอะไรโดยรวม ดู `docs/exam-prep/FILE_GUIDE.md`

โครงสร้างโดยย่อ: หน้าเว็บ `streamlit_app.py` + `ui/` → ชั้นบริการ `service.py` → ฐานข้อมูล `db.py` ส่วนบอทเป็นอีกโปรเซส (`bot_service.py` ควบคุม `bot/meet_engine.py`) หน้าเว็บคุยกับบอทผ่าน HTTP ที่ `bot/botclient.py`

---

## ฟังก์ชัน 1: เชื่อมต่อห้องประชุมและดักจับคำบรรยายสด

| ข้อใน TOR | โค้ด (ไฟล์:บรรทัด) | จุดที่ควรเปิดอ่านเพื่อดูว่าทำงานจริง | เทสต์ | หมายเหตุ |
|---|---|---|---|---|
| Host นำเข้าลิงก์ Meet ผ่านหน้าเว็บ | `ui/new_meeting.py:133` (ช่องลิงก์) `:54` `_create` · `service.py:55` `check_meet_url` · `service.py:89` `create_meeting` · `db.py:383` `create_meeting_setup` | `check_meet_url` ตรวจรูปแบบลิงก์ก่อนบันทึก | `test_service`: `test_meet_links_with_the_meet_path_prefix_and_query_are_accepted_and_match_duplicates` | เตือนลิงก์ซ้ำที่ `service.py:171` `meetings_with_url` |
| สั่งบอทเริ่มทำงาน | `ui/tab_bot.py:10` `_start` (เรียก `botclient.start` บรรทัด 12), ปุ่ม `:146` · `bot/botclient.py:100` `start` · `bot_service.py:279` `bot_start` · `bot/meet_engine.py:307` `start` | หน้าเว็บเรียกบริการบอทผ่าน HTTP แนบโทเคนลับ (`botclient.py:25` `get_token`) | `test_bot_service`: `test_chair_by_email_can_start_and_stop`, `test_requests_without_the_secret_token_are_rejected`, `test_only_one_meeting_at_a_time_and_others_cannot_see_or_stop_it` | บอททำงานทีละห้องต่อเครื่อง (ข้อจำกัดที่เขียนใน Proposal) |
| ใช้ Playwright เข้าห้อง | `bot/meet_engine.py:601` `_run` · `:611` เปิด Chrome แบบ persistent profile (`.meet_profile/` ที่ล็อกอินไว้) · `:654` กดปุ่มเข้าร่วม (`JOIN_BUTTON_RE` `:272`) · `:675` รอผู้จัดอนุญาต (`JOIN_WAIT_S` `:283` = 1200 วินาที) | ผู้จัดประชุมต้องกดอนุญาตเอง บอทรอสูงสุด 20 นาที | `test_join_button`: ปุ่มเข้าห้องรวมหน้า "เปลี่ยนที่นี่" (จำลองใน Chromium) | ทดสอบกับห้อง Meet จริงโดยคุณเอง ผมไม่มีบันทึกผลจริงในโปรเจกต์ |
| เปิด Closed Captions อัตโนมัติ | `bot/meet_engine.py:372` `_caption_state` · `:401` `_enable_captions` · `:424` `_ensure_captions` (`CAPTION_WAIT_S` `:284`) | ตรวจ "จากหน้าจอจริง" ก่อนรายงานว่าเปิดสำเร็จ ไม่เชื่อแค่การกดปุ่ม | ไม่พบเทสต์ที่ชื่อตรง (ครอบคลุมในหน้าจำลองของ `test_caption_watcher`/`test_join_button` บางส่วน ผมไม่ได้ยืนยัน) | เปิดไม่ได้ตอนอยู่ห้องรอเพราะปุ่มยังไม่ขึ้น จึงวนลองจนผู้จัดอนุญาต |
| ตรวจสถานะและออกจากห้องเมื่อประชุมจบ | `bot/meet_engine.py:501` `_monitor` · `WATCHDOG_INTERVAL_S` `:278` = 15 วินาที · `LEFT_ROOM_STRIKES` `:279` = 3 รอบ · `:319` `stop` · `bot_service.py:316` `bot_stop` · `:211` `_finalize` | ตรวจว่าหน้า ตัวจับคำบรรยาย กรอบคำบรรยาย ปุ่มวางสายยังอยู่ ถ้าหลุดจะปิดการบันทึกเรียบร้อย ข้อความที่บันทึกแล้วไม่หาย | จำลองด้วย `tools/soak_sim.py lifecycle` (ประชุมจบ/หน้าปิด/โหลดใหม่) ไม่มีเทสต์ใน `tests/` | `soak_sim` ไม่ใช่ Meet จริง |

---

## ฟังก์ชัน 2: ดักจับและจัดระเบียบข้อความคำบรรยายสด

| ข้อใน TOR | โค้ด (ไฟล์:บรรทัด) | จุดที่ควรเปิดอ่านเพื่อดูว่าทำงานจริง | เทสต์ | หมายเหตุ |
|---|---|---|---|---|
| ดักจับข้อความและชื่อผู้พูดแบบเรียลไทม์ | `bot/meet_engine.py:29-221` JavaScript ที่ฝังในหน้า Meet (`MutationObserver` `:201`, จุดยึด `ANCHOR` `:86`) · `:461` `_install_watcher` · `:341` `_emit` | `ANCHOR` คือกลไกแก้ปัญหา Meet ตัดข้อความหน้าแล้วแก้คำย้อนหลัง ไม่ให้ข้อความต่อซ้ำ | `test_caption_watcher` (4 ข้อ) เช่น sliding window ไม่ซ้ำไม่หาย, การแก้คำภายในหน้าต่าง (จำลองใน Chromium จริง) | ตัวจับพึ่งโครงสร้างหน้า Meet ที่ Google เปลี่ยนได้ไม่แจ้ง |
| รับเหตุการณ์และบันทึกลง MySQL | `bot_service.py:201` `_on_engine_event` · `:162` `_handle_event` · `:148` `_save_worker_loop` (คิวเขียนตัวเดียว) · `:99` `_persist_segment` · `db.py:976` `insert_segment` · `:991` `update_segment` · `db.py:723` `get_or_create_speaker` | ข้อความยาวเกิน 3,000 ตัวอักษรแบ่งหลาย segment (`bot_service.py:35` `_SEGMENT_MAX_CHARS`, `:92` `_cut_point`) ไม่เก็บไฟล์เสียง | `test_bot_service`: `test_one_endless_monologue_is_split_into_segments_not_lost`, `test_resume_continues_sequence_numbers` | เก็บเฉพาะข้อความ ไม่มีโค้ดบันทึกเสียงที่ผมพบ |
| อ่านรายชื่อผู้เข้าร่วมในห้อง | `bot/meet_engine.py:228-261` `JS_READ_PARTICIPANTS` (อ่านแผง "บุคคล") · `:470` `_scan_participants` · `:492` `_final_scan` · `:273` `PARTICIPANT_SCAN_S` = 60 | อ่านทันทีที่เข้าห้อง ทุก 60 วินาที และตอนหยุดบอท (ลูปใน `_monitor` ประมาณบรรทัด 523-570) | `test_participant_scan`: `ParticipantScanTests` (6 ข้อ), `ParticipantScanTimingTests` (2 ข้อ) | **ส่วนที่อาจารย์ขอดูโดยเฉพาะ** ทดสอบกับห้อง Meet จริงแค่ 1 ห้อง 2 คน (ตาม Proposal หน้า 70) |
| ตั้งสถานะ "เข้าร่วม" ให้ผู้ที่ชื่อตรง | `bot_service.py:139` `_persist_participants` → `db.py:675` `record_room_names` → `db.py:655` `mark_present_by_names` · `db.py:702` `_match_key`, `:708` `_find_speaker_for_name` (จับคู่เฉพาะ "ชื่อภาษาอังกฤษ" `meet_alias`) · `db.py:940` `rematch_meet_speakers` | ไม่เดาชื่อ ถ้าไม่ตรงจะเป็นแถว source = meet ให้ผู้ใช้ยืนยันที่แท็บ ③ | `test_db`: `test_mark_present_by_names_only_changes_registered_invited_people`, `test_unknown_room_names_are_kept_as_meet_guests_so_they_can_be_matched_later` · `test_service`: `test_people_table_leaves_meet_names_alone_and_rematches_after_the_english_name...` | ตอนอ่านรายชื่อระบบตั้งได้แค่ "เข้าร่วม" ไม่ตั้ง "ไม่มา" (ดูฟังก์ชัน 3) |

---

## ฟังก์ชัน 3: ตรวจทานและยืนยันข้อความถอดเสียง

| ข้อใน TOR | โค้ด (ไฟล์:บรรทัด) | จุดที่ควรเปิดอ่านเพื่อดูว่าทำงานจริง | เทสต์ | หมายเหตุ |
|---|---|---|---|---|
| แก้ข้อความ เปลี่ยนผู้พูด ลบช่วง | `ui/tab_transcript.py:127` `render`, บันทึกที่ `:189` → `service.py:388` `apply_segments_table` → `db.py:1058` `edit_segment` | เก็บข้อความต้นฉบับใน `original_text` ช่วงที่ลบเป็นแบบซ่อน (`deleted`) กู้คืนได้ และไม่ถูกส่งให้ AI | `test_db`: `test_segment_edit_keeps_original_and_deleted_are_excluded` | – |
| รวมชื่อที่ Meet แสดงให้ตรงรายชื่อ | `ui/tab_transcript.py:91,118` → `service.py:306` `merge_people` → `db.py:911` `merge_speakers` | ย้ายข้อความทั้งหมดของชื่อนั้นไปรวมกับคนที่ลงทะเบียนไว้ และจำชื่อ Meet ไว้เป็น alias | `test_db`: `test_merge_speakers_moves_segments_and_remembers_alias` · `test_service`: `test_merge_people_fixes_a_name_mismatch` | – |
| ยืนยันผู้เข้าร่วมให้ครบ (มา / ไม่มา / จับคู่ / นอกรายชื่อ) | `ui/tab_transcript.py:54` `_confirm_section` · `service.py:279` `set_person_attendance`, `:291` `confirm_guest`, `:298` `exclude_person` · `db.py:875` `exclude_meet_speaker`, `:863` `confirm_meet_speaker_as_guest`, `:892` `list_unresolved` | ยืนยัน transcript ไม่ได้จนกว่าจะตอบครบ: คนที่ลงทะเบียนไว้แต่ยัง "เชิญไว้" ถือเป็นรายการค้าง (`db.py:892` `list_unresolved`, ด่านที่ `service.py:421` `verify_transcript`) จึงต้องผู้ใช้เป็นคนตอบ มา/ไม่มา ทุกคน | `test_service`: `test_each_outsider_can_be_matched_confirmed_as_guest_or_excluded`, `test_verify_is_blocked_until_everyone_is_confirmed_then_database_matches_...` | – |
| ยืนยัน transcript ก่อนให้ AI | `ui/tab_transcript.py:209` → `service.py:421` `verify_transcript` · `service.py:440` `reopen_transcript` · ด่านที่ `service.py:548` `generate_report` (ต้องยืนยันแล้ว) | ถ้ายังตอบไม่ครบหรือยังไม่ยืนยัน ระบบไม่ให้ AI ร่างรายงาน | `test_service` (เช่นเทสต์ข้างบน) · `test_ui`: `test_roster_table_is_locked_after_start_and_outsiders_are_confirmed_one_by_one` | ตารางรายชื่อถูกล็อกระหว่างบอทบันทึก (`service.py:188` `roster_locked`, เทสต์ `test_roster_is_locked_while_the_bot_records_and_editable_again_after_stop`) |

---

## ฟังก์ชัน 4: AI สรุปเนื้อหาและสกัดภาระงาน

| ข้อใน TOR | โค้ด (ไฟล์:บรรทัด) | จุดที่ควรเปิดอ่านเพื่อดูว่าทำงานจริง | เทสต์ | หมายเหตุ |
|---|---|---|---|---|
| วิเคราะห์ transcript ด้วย LLM | `service.py:548` `generate_report` → `reports/summarizer.py:517` `generate_for_meeting` → `:435` `generate_minutes_content` → `:386` `_gemini_generate` | ประชุมเกิน 60,000 ตัวอักษร (`:44` `SINGLE_PASS_CHARS`) แบ่งช่วงสกัดก่อนแล้วเรียบเรียง (map-reduce `:459`, `split_chunks` `:369`, ช่วงละ 30,000 `:45`) ลองซ้ำเมื่อ Gemini ล้มเหลวชั่วคราว (`:413`) | `test_summarizer`: `test_long_meeting_uses_map_reduce`, `test_split_chunks_respects_limit_and_keeps_order`, `test_bad_ai_output_is_retried_once` | ใช้โควตา Gemini ระดับฟรี (ต่อวันต่อโมเดล) ผมไม่ได้ทดสอบเรียก Gemini จริงในรอบเขียนเอกสารนี้ |
| สกัดภาระงาน (รายละเอียด ผู้รับผิดชอบ กำหนดส่ง) | `reports/summarizer.py:57-102` สคีมา `AgendaItemAI`, `ActionItemAI`, `CarriedItemAI`, `MinutesBodyAI` · `prompts/minutes_v7.md` (ตั้งที่ `summarizer.py:28`) | ส่งวันที่ประชุมให้ AI เพื่อแปลง "วันพฤหัสหน้า" เป็นวันที่จริง ผู้รับผิดชอบต้องอยู่ในรายชื่อ (`match_participant_name` `:150`) | `test_summarizer`: `ValidateTests` (20 ข้อ) | รอบ 135 AI ไม่สกัดงาน "ขอใบเสร็จ" ที่พูดชัด (ข้อจำกัดความแม่นยำ ไม่ใช่ 100%) |
| โครงสร้าง JSON Schema พร้อมใช้งานต่อ | `summarizer.py:404` บังคับ `response_schema` ส่งให้ Gemini · `:422` `_parse_body` · `:187` `validate_minutes` · `:130` `grounding_score` (เกณฑ์ `GROUNDED_THRESHOLD` `:49` = 0.8) | AI ตอบตามสคีมาที่กำหนด แล้วตรวจซ้ำ: ข้อความอ้างอิงต้องหาเจอใน transcript จริง ไม่ผ่านจะติดคำเตือน | `test_summarizer`: `test_fabricated_evidence_is_flagged`, `test_grounding_ignores_thai_spacing`, `test_section_outside_the_two_ai_choices_is_rejected_by_the_schema` | ผลลัพธ์เก็บต่อใน MySQL, แสดงให้แก้ใน UI, ส่งเข้า Calendar |
| (เสริม) วาระ 2, 3, 4.1 จากการประชุมครั้งก่อน | `reports/continuity.py:81` `prefill_items` (สร้างโครงจาก DB) · `:105` `prompt_list` · `:118` `apply_carried` (เติมสิ่งที่ AI ตอบ ข้ามรายการที่ไม่มีรหัสหรือไม่มีหลักฐาน) · ประกอบใน `summarizer.py:535-539` | ข้อเท็จจริงมาจาก DB ส่วนความคืบหน้า/มติ AI เติมเฉพาะที่พูดจริงและมีหลักฐาน | `test_service`: `test_generating_a_report_prefills_sections_2_3_and_4_1_from_the_previous_meeting`, `test_ai_fills_progress_and_resolution_of_the_carried_items_but_only_with_evidence`, `test_without_a_previous_meeting_the_ai_gets_no_carried_list_and_its_carried_answers_are_ignored` | คุณแจ้งว่าทดสอบกับ Gemini จริงแล้วว่าเติมวาระ 2, 3, 4.1, 5 ได้ (ผมไม่ได้เห็นผลนั้นเอง) |
| (เสริม) "เรื่องอื่นๆ" วาระ 5 | `summarizer.py` ฟิลด์ `other_matters`/`other_evidence` · เก็บเป็นแถว `agenda_items` หมวด `other` (`db.py:1171` `_move_other_matters_to_agenda`) · แสดงที่ `ui/tab_report.py` (หัวข้อ "วาระ 5") | ต้องมีข้อความอ้างอิงตรวจกับ transcript เหมือนวาระอื่น | `test_summarizer`: `test_other_matters_is_checked_for_evidence_like_the_agenda` · `test_db`: `test_other_matters_is_stored_as_one_agenda_row_not_a_summaries_column` · `test_ui`: `test_other_matters_shows_its_evidence_and_warns_when_it_has_none` | – |

---

## ฟังก์ชัน 5: แสดงผลสรุปสำหรับตรวจสอบ (Summary Panel)

| ข้อใน TOR | โค้ด (ไฟล์:บรรทัด) | จุดที่ควรเปิดอ่านเพื่อดูว่าทำงานจริง | เทสต์ | หมายเหตุ |
|---|---|---|---|---|
| แสดงสรุปและตารางภาระงานบนหน้าเว็บ | `ui/tab_report.py:361` `render` · `:239` `_agenda_cards` · `ui/tab_calendar.py:166` `render` (ตารางงานแท็บ ⑤) · `service.py:495` `get_report_view` | วาดคำเตือนและหลักฐานที่ตรวจแล้ว (✓/⚠) ที่คำนวณสดทุกครั้งที่เปิด (`service.py:487` `_validated`) | `test_ui`: `test_draft_report_has_a_section_selector_per_agenda_item_and_setup_offers_previous_meetings` | หน้าวาระ 1–4 กับวาระ 5 แยกหัวข้อกัน (ตั้งใจให้ตรงโครงสร้างข้อมูล) |
| บันทึกลง MySQL ทันทีหลัง AI เสร็จ และแก้ได้ทุกช่อง | `db.py:1367` `save_report` · `:1403` `update_report_content` · `service.py:573` `save_report_draft` | ฉบับร่างแก้ได้ ฉบับอนุมัติแล้วล็อก (`db.py:1447` `approve_report`) | `test_service`: `test_approved_report_cannot_switch_drafts_until_reopened` · `test_ui`: `test_approved_report_is_locked_and_offers_reopen_and_calendar` | – |
| เก็บทุกฉบับเป็นประวัติแบบเพิ่มอย่างเดียว | `db.py:1218` `_record_version` (ตาราง `summary_versions`) · `service.py:517` `report_history`, `:534` `use_report_version` · `ui/tab_report.py:178` `_history_panel`, `:127` `_show_diff` · `reports/history.py:108` `diff_reports` | ทุกการ AI ร่าง/แก้/อนุมัติ/ยกเลิกอนุมัติ เพิ่มฉบับใหม่ ไม่เขียนทับ เปรียบเทียบและเลือกฉบับเก่ามาใช้ได้ขณะเป็นฉบับร่าง | `test_service`: `test_history_records_who_saved_each_draft_with_their_google_name_and_email`, `test_history_is_private_to_people_who_can_see_the_meeting` · `test_history` (16 ข้อ) · `test_ui`: `test_report_tab_shows_edit_history_and_which_draft_is_in_use` | – |

---

## ฟังก์ชัน 6: ส่งออกและแจ้งเตือนภาระงานภายนอก

| ข้อใน TOR | โค้ด (ไฟล์:บรรทัด) | จุดที่ควรเปิดอ่านเพื่อดูว่าทำงานจริง | เทสต์ | หมายเหตุ |
|---|---|---|---|---|
| อนุมัติแล้วส่งภาระงานเข้า Calendar พร้อมกัน | `ui/tab_calendar.py:137` `_approve_dialog` (เรียก `approve_and_send` บรรทัด 156) · `service.py:751` `approve_and_send` → `:593` `approve_report` + `:690` `sync_calendar` · `db.py:1447` `approve_report` | อนุมัติ + ส่งนัดในครั้งเดียว ส่งไม่สำเร็จกด "ส่งงานที่ยังไม่ส่ง" (`ui/tab_calendar.py:259`) ซ้ำได้ | `test_service`: `test_approve_and_send_creates_events_for_dated_items_with_attendees_and_stores...` | เจอจริงวันที่ 11 พ.ย.: อีเมลผู้รับผิดชอบใน roster พิมพ์ผิด Google ตอบ 400 Invalid attendee email งานนั้นส่งไม่ผ่านทั้งงาน (ผมยังไม่ได้เปิดโค้ดจัดการข้อผิดพลาดว่าตั้งใจให้ข้ามอีเมลผิดหรือไม่) |
| สิทธิ์ OAuth 2.0 และสร้างนัดอัตโนมัติ | `integrations/calendar_auth.py:24` `SCOPES` (`calendar.events`) · `:48` `build_auth_url` · `:58` `exchange_code` · `:76` `get_credentials` · `integrations/calendar_sync.py:108` `create_event` · `:35` `build_event_body` · token เก็บที่ `db.py:343` `save_calendar_token` (คอลัมน์ `users.calendar_token`) | ขอสิทธิ์ `calendar.events` แยกจากสิทธิ์ล็อกอิน ผู้ใช้กดเชื่อมที่แถบซ้ายเอง (flow ผ่าน `bot_service.py:349` `calendar_connect` และ `:358` `calendar_callback`) นัดเข้าปฏิทินของผู้กดอนุมัติ | `test_calendar` (23 ข้อ) · `test_bot_service`: `test_calendar_connect_requires_a_valid_signed_token`, `test_calendar_callback_saves_token_for_the_signed_user_and_returns_only_to_our_ui` | token ไม่ได้เข้ารหัสใน DB (ข้อจำกัดที่รู้อยู่) คำอธิบายหัวไฟล์ `calendar_auth.py` ยังเขียนถึงตาราง `calendar_tokens` ซึ่งไม่มีแล้ว |
| เก็บประวัติการประชุมให้เปิดดูย้อนหลัง | `ui/home.py:57` `render` · `db.py:587` `list_meetings` · `service.py:125` `list_meetings` · `service.py:236` `delete_meeting` · `:129` `meeting_credit` | แสดงผู้สร้างและผู้อนุมัติ ลบการประชุมได้ทุกสถานะยกเว้นขณะบอทบันทึก (ลบนัดใน Calendar ด้วยถ้าอนุมัติแล้ว) และเตือนถ้ามีประชุมอื่นใช้เป็น "ครั้งก่อน" (`service.py:228`) | `test_service`: `test_delete_approved_meeting_removes_calendar_events_or_refuses`, `test_a_chair_sees_who_created_the_meeting_they_can_open` | – |
| ช่วงเวลา / หลายวัน (เวลาเริ่ม-สิ้นสุด วันที่สิ้นสุด) | `integrations/calendar_sync.py:35` `build_event_body` · คอลัมน์ `action_items.due_date_end`, `due_time`, `due_time_end` | นัดเต็มวัน หลายวัน หรือช่วงเวลา (วันสิ้นสุดแบบเต็มวันของ Google เป็นแบบไม่รวมวันสุดท้าย แปลงให้แล้ว) | `test_calendar`: `test_all_day_end_is_exclusive_in_google_and_inclusive_in_meetsync`, `test_multi_day_timed_event_has_an_end_date`, `test_late_start_rolls_over_midnight_instead_of_ending_before_it_starts` | – |
| ตรวจนัดที่ส่งไปแล้วและปรับงานตามเมื่อผู้ใช้ยืนยัน | `service.py:711` `check_calendar_changes` · `:738` `apply_calendar_changes` · `integrations/calendar_sync.py:131` `event_schedule`, `:150` `fetch_event`, `:162` `compare_with_google` · `db.py:1526` `apply_calendar_changes` · `ui/tab_calendar.py:84` `_calendar_changes` | ตรวจเมื่อเปิดหน้าหรือกดปุ่ม ไม่ใช่เรียลไทม์ แก้เฉพาะชื่อและวัน/เวลา ไม่ซิงก์ผู้รับผิดชอบ นัดที่ถูกลบจะล้างลิงก์ งานไม่ถูกลบ | `test_service`: `test_unchanged_events_are_reported_as_the_same`, `test_attendees_changed_in_google_do_not_change_the_assignee`, `test_event_deleted_in_google_is_flagged_and_its_link_cleared_on_apply_but_the_task_stays` | README ระบุว่าทดสอบกับตัวจำลอง ยังไม่ได้ทดลองกับ Google จริงเป็นหลักฐาน |
| ส่งออกรายงานเป็น PDF (หัวข้อตัวหนา เนื้อความปกติ) | `service.py:664` `build_pdf` → `reports/pdf_report.py:192` `build_minutes_pdf` · `reports/report_data.py:75` `build_header` · `reports/report_template.py` · `reports/thai_text.py:42` `break_thai` | A4 ขอบ 72 pt ฟอนต์ TH Sarabun New ตัดคำไทยไม่ตัดกลางคำ ยังไม่อนุมัติมีลายน้ำ "ฉบับร่าง" ชื่อไฟล์ตามหัวข้อประชุม (`service.py:653` `pdf_filename`) | `test_service`: `test_pdf_places_each_item_under_its_own_agenda_heading`, `test_pdf_due_column_shows_a_date_range_when_there_is_an_end_date`, `test_pdf_file_name_is_the_meeting_title` · `test_thai_text` (7 ข้อ) | คำไทยคำเดียวที่ยาวกว่าช่องแคบยังถูกตัดกลางคำได้ (ข้อจำกัดที่เขียนไว้) |

---

## สิทธิ์การเข้าถึง (ไม่ได้อยู่ในตารางฟังก์ชัน แต่อาจารย์อาจถาม)

| เรื่อง | โค้ด | เทสต์ |
|---|---|---|
| เจ้าของ หรือประธาน/เลขาตามอีเมลเท่านั้นที่จัดการประชุมได้ | `db.py:565` `is_meeting_manager` · `service.py:36` `meeting_for` · `bot_service.py:260` `_manager_of` | `test_service`: `test_other_users_cannot_see_or_touch_a_meeting`, `test_chair_by_email_can_manage_but_plain_attendee_cannot` |
| ล็อกอิน Google (ขอแค่ openid email profile) | `ui/auth.py:62` `current_user`, `:94` `require_login` · `db.py:323` `upsert_user` | ไม่พบเทสต์ที่ชื่อตรง (ล็อกอินจริงเป็นของ Streamlit) |
| โทเคนลับระหว่างหน้าเว็บกับบริการบอท | `bot/botclient.py:25` `get_token` · `bot_service.py:242` `identity` | `test_bot_service`: `test_requests_without_the_secret_token_are_rejected` |

---

## ข้อควรรู้ก่อนไล่เทียบกับ TOR

1. **TOR v2 หัวข้อ 3.1 (ผู้จัดประชุม) ยังเขียนข้อความเก่า** ("เลือกส่งภาระงานไปยัง Google Calendar ของตนเองได้" และ "ยังไม่สามารถแก้ไขเนื้อหาสรุปที่ AI สร้างได้ในเวอร์ชันปัจจุบัน") ซึ่งขัดกับฟังก์ชัน 5 และ 6 ใน TOR v2 เอง และไม่ตรงกับระบบ (ระบบส่งทั้งหมดพร้อมกันตอนอนุมัติ และแก้ได้ทุกช่อง) ถ้าอาจารย์ไล่ตามตาราง ควรบอกเรื่องนี้ก่อน
2. **TOR v1 → v2 มีการเปลี่ยนความต้องการ**: v1 ให้ผู้ใช้เลือกส่งภาระงานแต่ละรายการผ่านปุ่ม "ส่งไป Calendar" ส่วน v2 ส่งทั้งหมดพร้อมกันเมื่อกดอนุมัติ ระบบทำตาม v2 (ยังมีปุ่มส่งซ้ำเฉพาะงานที่ส่งไม่สำเร็จ)
3. **ความแม่นยำของ AI ไม่ใช่ 100%**: รอบทดสอบ 1 ชั่วโมง (ประชุม 135) วาระที่พูดจริงถูกต้อง งาน 5 รายการถูกหมด แต่ AI ไม่สกัดงาน "ขอใบเสร็จ" ที่พูดชัด และผู้พูดไม่ได้พูดครบตามบท เป็นเหตุให้วาระ 6, 7 หายไป (ไม่ใช่ความผิดของ AI)
4. **ที่ผมยืนยันได้จริงในรอบเขียนเอกสารนี้**: ชื่อฟังก์ชันและเลขบรรทัดตรงกับโค้ด, เทสต์ 311 ข้อผ่านวันที่ 11 พ.ย. 2569, จำลองตัวจับคำบรรยาย 10 นาทีผ่าน (123 ประโยค จับครบ ไม่ผิด หน่วยความจำไม่เพิ่มต่อเนื่อง) ส่วนการทำงานกับ Google Meet, Gemini และ Google Calendar จริงผมไม่ได้รันเอง ต้องอ้างผลทดสอบของคุณ
5. **ข้อยกเว้นเล็กๆ ของ "ระบบไม่ตั้ง ไม่มา ให้เอง"**: `db.mark_unconfirmed_absent` (`db.py:786`) เปลี่ยนคนที่ยัง "เชิญไว้" เป็น "ไม่มา" ให้อัตโนมัติ เรียกสองที่คือหลังยืนยัน transcript (`service.py:434`, ตอนนั้นไม่เหลือใครค้างเพราะต้องตอบครบก่อน) และตอนอนุมัติรายงาน (`service.py:606`) เพื่อคนที่เพิ่มรายชื่อหลังยืนยัน transcript จะได้ไม่ค้างเป็น "ยังไม่ยืนยัน" ในรายงานที่ล็อก ดังนั้นประโยคใน Proposal/TOR ว่า "ผู้ไม่มาผู้ใช้เป็นผู้ยืนยันเท่านั้น" เป็นจริงในขั้นยืนยัน transcript แต่ถ้ามีคนเพิ่มทีหลังและไม่ตอบ ระบบจะตั้ง "ไม่มา" ให้ตอนอนุมัติ (ผมไม่ได้ตัดสินว่าต้องแก้ข้อความหรือโค้ด)
6. ในโค้ดมีคำอธิบายที่ล้าสมัยหลายจุด (ดูหัวข้อ 6 ใน `docs/exam-prep/FILE_GUIDE.md`) ถ้าอาจารย์เปิดอ่านโค้ดและเจอ ให้รู้ไว้ว่าเป็นข้อความอธิบายเก่า ไม่ใช่พฤติกรรมของระบบ

## ลำดับแนะนำถ้าจะเปิดโค้ดไล่ให้อาจารย์ดูทีละฟังก์ชัน

1. ฟังก์ชัน 1: `ui/new_meeting.py` → `service.create_meeting` → `ui/tab_bot.py` → `bot/botclient.py` → `bot_service.bot_start` → `bot/meet_engine._run`
2. ฟังก์ชัน 2: `meet_engine.JS_CAPTION_WATCHER` → `bot_service._handle_event` → `db.insert_segment`; แล้ว `JS_READ_PARTICIPANTS` → `_persist_participants` → `db.mark_present_by_names`
3. ฟังก์ชัน 3: `ui/tab_transcript.py` → `service.apply_segments_table`/`verify_transcript` → `db.edit_segment`
4. ฟังก์ชัน 4: `service.generate_report` → `summarizer.generate_minutes_content` → `validate_minutes` และ `prompts/minutes_v7.md`
5. ฟังก์ชัน 5: `ui/tab_report.py` → `db.save_report` / `_record_version` → `reports/history.py`
6. ฟังก์ชัน 6: `ui/tab_calendar._approve_dialog` → `service.approve_and_send` → `calendar_sync.create_event` และ `reports/pdf_report.build_minutes_pdf`
