# การทำงานของ Google Calendar ใน MeetSync: ใช้อะไร ทำงานอย่างไร และมาจากโค้ดไหน

เอกสารนี้อธิบายตั้งแต่การเชื่อมต่อบัญชี (OAuth) การส่งภาระงานเข้า Calendar การตรวจนัดกลับมาเทียบ ไปจนถึงการลบนัด โดยระบุไฟล์และเลขบรรทัดของโค้ดที่ทำแต่ละขั้น
เลขบรรทัดตรวจจากโค้ด ณ commit `a080b13` (วันที่ 12 พ.ย. 2569) ถ้าแก้โค้ด เลขเลื่อน ให้ค้นด้วยชื่อฟังก์ชัน

ไฟล์หลักที่เกี่ยวข้อง: `integrations/calendar_auth.py` (OAuth), `integrations/calendar_sync.py` (สร้าง/อ่าน/เทียบ/ลบนัด), `service.py` (ลำดับขั้นและสิทธิ์), `bot_service.py` (รับ callback ของ Google), `bot/botclient.py` (โทเคนที่เซ็นแล้ว), `ui/tab_calendar.py` (หน้าจอแท็บ ⑤), `db.py` (เก็บ token และสถานะนัด)
ไฟล์คู่กัน: `docs/subsystem-guides/PLAYWRIGHT_BOT_GUIDE.md` (การทำงานของบอท), `docs/exam-prep/ADVISOR_PREP.md` (เตรียมตอบอาจารย์), `docs/exam-prep/TOR_MAP.md` (ฟังก์ชัน 6 ของ TOR)

ข้อความที่เป็น "ความรู้ทั่วไป" ของ OAuth/Google API (ไม่ได้มาจากโค้ดของโปรเจกต์) ผมกำกับไว้ว่า "ทั่วไป"

---

## 0. สรุปสั้น (พูดได้ใน 30 วินาที)

ผู้ใช้กดปุ่ม "เชื่อมต่อ Google Calendar" ที่แถบซ้าย ระบบพาไปหน้าขออนุญาตของ Google (OAuth 2.0) ขอสิทธิ์ระดับกิจกรรมของปฏิทิน (`calendar.events`) เท่านั้น เมื่ออนุญาตแล้ว Google ส่งรหัสกลับมาที่บริการบอท ระบบแลกเป็น token แล้วเก็บใน MySQL แยกรายผู้ใช้ เมื่อผู้ใช้กดอนุมัติรายงาน ระบบเรียก Google Calendar API สร้างนัดทีละงานที่มีวันกำหนดในปฏิทินของผู้ที่กดส่ง (เต็มวัน หลายวัน หรือช่วงเวลา) ถ้างานไหนส่งไม่ผ่าน งานอื่นยังส่งต่อได้ และกดส่งซ้ำเฉพาะที่ค้างได้ ภายหลังผู้ใช้ยกเลิกอนุมัติ ระบบลบนัดออก และถ้าผู้ใช้แก้ชื่อหรือเวลาของนัดใน Calendar เอง ระบบตรวจเจอเมื่อเปิดหน้า และปรับงานตามเมื่อผู้ใช้กดยืนยัน

---

## 1. ใช้อะไรบ้าง

| ส่วน | คืออะไร | ที่ไหนในโปรเจกต์ |
|---|---|---|
| **OAuth 2.0 (ทั่วไป)** | มาตรฐานให้ผู้ใช้อนุญาตให้แอปเข้าถึงข้อมูลของตนโดยไม่ต้องบอกรหัสผ่านให้แอป ได้ "token" แทน | `integrations/calendar_auth.py` |
| **Google Calendar API v3 (ทั่วไป)** | บริการของ Google ให้โปรแกรมสร้าง/อ่าน/แก้/ลบนัดในปฏิทิน | `integrations/calendar_sync.py:97-101` สร้างไคลเอนต์ด้วย `build("calendar", "v3", credentials=creds)` |
| `google-auth-oauthlib` (`Flow`) | ไลบรารีทำขั้นตอน OAuth (สร้างลิงก์ขออนุญาต แลกรหัสเป็น token) | `calendar_auth.py:20`, `:39-45` |
| `google-auth` (`Credentials`, `RefreshError`) | เก็บ/ต่ออายุ token | `calendar_auth.py:17-19`, `:76-89` |
| `google-api-python-client` (`build`) | ตัวเรียก Calendar API | `calendar_sync.py:6` |
| `itsdangerous` | เซ็นโทเคนที่ใช้เป็น `state` ของ OAuth (กันปลอมและกันหมดอายุ) | `bot/botclient.py:16`, `:45-59` |
| FastAPI (บริการบอท) | รับ `redirect` กลับจาก Google | `bot_service.py:348` `/calendar/connect`, `:357` `/auth/google/callback` |
| Streamlit | ปุ่มเชื่อม ปุ่มอนุมัติ ตารางงาน | `ui/common.py:196` `_calendar_chip`, `ui/tab_calendar.py` |
| MySQL | เก็บ token (`users.calendar_token`) และสถานะนัดของแต่ละงาน (`action_items.google_calendar_event_id`, `google_calendar_link`, `invite_sent`) | `db.py:43` (คอลัมน์), `:343-377` (token), `:1555` (สถานะนัด) |

ตัวแปรใน `.env` ที่เกี่ยวข้อง: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (`calendar_auth.py:31-32`), `CALENDAR_SEND_INVITES` (ค่าเริ่มต้นของช่อง "ส่งอีเมลเชิญ" `ui/tab_calendar.py:56`), `BOT_PUBLIC_URL` / `STREAMLIT_URL` (`botclient.py:19-20`)

---

## 2. สองสิทธิ์ที่แยกกัน: ล็อกอิน กับ Calendar

| | ล็อกอินเข้า MeetSync | เชื่อม Calendar |
|---|---|---|
| ทำโดย | Streamlit `st.login` (OIDC) | โค้ดของเราเอง (`calendar_auth.py`) |
| สิทธิ์ที่ขอ | ระบุตัวตนเท่านั้น (`openid`, `email`, `profile`) | `calendar.events` เท่านั้น (`calendar_auth.py:24`) |
| redirect URI | `http://localhost:8501/oauth2callback` (`tools/make_streamlit_secrets.py`) | `http://localhost:8000/auth/google/callback` (`calendar_auth.py:25`) |
| ที่เก็บผล | แถว `users` (ชื่อ อีเมล) `db.py:323` `upsert_user` | `users.calendar_token` `db.py:343` `save_calendar_token` |
| ผู้ใช้ทำเมื่อไร | ทุกครั้งที่เข้าระบบ | ครั้งเดียว (หรือเมื่อ token หมดอายุ/ถูกเพิกถอน) |

เหตุผลที่แยก: `st.login` ไม่ส่ง access token ของ Google ให้แอป (README หัวข้อ "ติดตั้งและรัน") และสิทธิ์ `calendar.events` แคบกว่าสิทธิ์จัดการปฏิทินทั้งหมด ผู้ใช้จึงเห็นว่าแอปขออะไร (ตาม Proposal)
ข้อควรรู้ (ทั่วไป): สิทธิ์ `calendar.events` ให้สร้าง/แก้/ลบกิจกรรมในปฏิทินได้ แต่ไม่ให้จัดการตัวปฏิทินหรือการตั้งค่าบัญชี Google

---

## 3. ขั้นตอนเชื่อมต่อบัญชี (OAuth) ทีละขั้น

```
ผู้ใช้ (หน้าเว็บ)                 บริการบอท (พอร์ต 8000)                  Google
1 กด "เชื่อมต่อ Google Calendar"
  ui/common.py:204
2 ลิงก์ที่เซ็นแล้ว ───────────►  /calendar/connect?t=<โทเคน> (bot_service.py:348)
  botclient.py:62                 ตรวจลายเซ็นและอายุ ≤ 10 นาที (:351)
                                  build_auth_url (calendar_auth.py:48) ──► หน้าขออนุญาต
3                                                                          ผู้ใช้กดอนุญาต
4                                 /auth/google/callback?code=...&state=... ◄── redirect กลับ
                                  (bot_service.py:357)
5                                 ตรวจ state ≤ 15 นาที (:360)
                                  exchange_code (calendar_auth.py:58) ──► แลก code เป็น token
                                  db.save_calendar_token (:61)
6 กลับหน้าเดิม ◄─── redirect ───  _safe_return (bot_service.py:343, :366)
```

| ขั้น | เกิดอะไรขึ้น | โค้ด |
|---|---|---|
| 1 | แถบซ้ายแสดงสถานะ "เชื่อมแล้ว/ยังไม่เชื่อม" ถ้ายังไม่เชื่อมแสดงปุ่มลิงก์ ลิงก์พากลับมาหน้าการประชุมเดิม (`?m=<id>`) | `ui/common.py:196-204` `_calendar_chip`, สถานะมาจาก `service.py:681` `calendar_connected` → `calendar_auth.py:64` `is_connected` |
| 2 | สร้างโทเคนที่เซ็นแล้วเก็บ `uid` (ผู้ใช้), `ret` (หน้าที่จะกลับไป), `em` (อีเมลที่ล็อกอิน) ใช้ `itsdangerous` โดยใช้ความลับร่วมของสองโปรเซส (`get_token`) และ salt `calendar-connect` | `bot/botclient.py:45-65` |
| 3 | บริการบอทตรวจลายเซ็นและอายุ (ไม่เกิน 600 วินาที) แล้วสร้างลิงก์ขออนุญาตของ Google | `bot_service.py:348-354` |
| 4 | ลิงก์ขออนุญาตมีค่า `access_type="offline"` (ให้ได้ refresh token ไว้ต่ออายุเอง), `prompt="consent"` (ได้ refresh token ทุกครั้ง), `state` = โทเคนที่เซ็นแล้ว, `login_hint` = อีเมลที่ล็อกอินอยู่ (Google เลือกบัญชีเดียวกันให้ ไม่ต้องเลือกซ้ำ) | `calendar_auth.py:48-55` |
| 5 | callback ตรวจ `state` (ลายเซ็น อายุไม่เกิน 900 วินาที) และต้องมี `code` มิฉะนั้นแจ้ง error | `bot_service.py:357-364` |
| 6 | แลก `code` เป็น token แล้วเก็บ `to_json()` ลงคอลัมน์ `users.calendar_token` ของผู้ใช้ใน `state` (รัน `exchange_code` ใน thread เพราะเป็นงานบล็อก) | `calendar_auth.py:58-61`, `bot_service.py:365` |
| 7 | redirect กลับหน้าเว็บ ตรวจว่าปลายทางขึ้นต้นด้วยที่อยู่หน้าเว็บของเราเท่านั้น (กัน open redirect) | `bot_service.py:343-345`, `:366` |

หมายเหตุด้านความปลอดภัยตามคอมเมนต์ในโค้ด: ปิด PKCE (`autogenerate_code_verifier=False` `calendar_auth.py:44`) เพราะ `build_auth_url` กับ `exchange_code` สร้าง `Flow` คนละอินสแตนซ์ในคนละ request ไม่มีที่เก็บ code_verifier ข้าม request และปิดได้เพราะเป็น confidential client (มี client secret) (คอมเมนต์ `:40-42`) ส่วนการกัน CSRF ใช้ `state` ที่เซ็นแล้ว

---

## 4. การเก็บและต่ออายุ token

- **เก็บ:** `db.save_calendar_token` (`db.py:343`) เขียน JSON ลง `users.calendar_token` (LONGTEXT) ของผู้ใช้คนนั้น (ชนิดคอลัมน์ `db.py:43`) JSON นี้มาจาก `flow.credentials.to_json()` ซึ่งตามปกติของไลบรารี google-auth มี access token, refresh token, ข้อมูลไคลเอนต์ และเวลาหมดอายุ (ผมไม่ได้เปิดดูค่าใน DB) **ไม่เข้ารหัส** เป็นข้อจำกัดที่รู้อยู่ เหมาะกับต้นแบบเครื่องเดียว
- **ใช้:** `calendar_auth.get_credentials(user_id)` (`:76`) อ่าน JSON กลับมาเป็น `Credentials` ถ้าหมดอายุและมี refresh token จะต่ออายุเองแล้วเขียนทับ (`:82-88`)
- **ถ้า Google ปฏิเสธการต่ออายุ** (หมดอายุจริงหรือผู้ใช้เพิกถอน): ล้าง token ใน DB ทันที (`db.clear_calendar_token` `:352`) แล้วโยน `CalendarReconnectNeeded` พร้อมข้อความให้กดเชื่อมต่อใหม่ (`:85-87`, `RECONNECT_MESSAGE` `:72`) เหตุที่ต้องล้าง: ไม่งั้นระบบจะคิดว่ายังเชื่อมอยู่และปุ่มเชื่อมต่อไม่ขึ้น (คอมเมนต์ `:86`)
- **แยกรายผู้ใช้:** นัดจะเข้าปฏิทินของ "ผู้ที่กดส่ง" ไม่ใช่ปฏิทินของคนที่เชื่อมทีหลังสุด (คอมเมนต์หัวไฟล์ `calendar_auth.py:9-11` อธิบายเรื่องนี้ แต่คอมเมนต์นั้นเขียนว่า token เก็บใน "ตาราง calendar_tokens" ซึ่งไม่มีตารางนี้แล้ว ปัจจุบันเก็บในคอลัมน์ `users.calendar_token` ข้อความในคอมเมนต์ล้าสมัย)

---

## 5. การส่งภาระงานเข้า Calendar ตอนอนุมัติ

```
ui/tab_calendar.py:137 _approve_dialog (ปุ่ม "อนุมัติและส่ง")
  └─ service.py:751 approve_and_send
       ├─ service.py:593 approve_report   อนุมัติ + ล็อกรายงาน + เก็บฉบับ approve
       └─ service.py:690 sync_calendar    วนทุกงานที่มีวันและยังไม่เคยส่ง
            └─ integrations/calendar_sync.py:108 create_event   (ทีละงาน)
                 ├─ attendee_email (:24)       หาอีเมลผู้รับผิดชอบจากรายชื่อ
                 ├─ build_event_body (:35)     สร้างเนื้อนัด
                 ├─ events().insert(...) (:124) เรียก Google Calendar API
                 └─ db.mark_action_item_synced (:127) จดรหัสนัด ลิงก์ และ invite_sent
```

### 5.1 `sync_calendar` (`service.py:690-708`)
- ต้องอยู่สถานะ `approved` เท่านั้น (`:694`)
- ข้ามงานที่ **มีรหัสนัดแล้ว** (กันนัดซ้ำ) หรือ **ไม่มีวันกำหนด** (`:698-699`)
- เรียก `create_event` ทีละงาน ถ้างานไหนผิดพลาด จดผลของงานนั้นเป็น `ok: False` พร้อมข้อความ **แล้วทำงานต่อ ไม่ทำให้งานอื่นล้ม** (`:701-707`) ทั้งสองแบบ: `RuntimeError`/`ValueError` (ข้อความเตรียมไว้ เช่น ยังไม่เชื่อม Calendar, ไม่มีวันกำหนด) และข้อผิดพลาดอื่น (เช่น Google ตอบ 400) แสดงเป็น "เขียนลง Calendar ไม่สำเร็จ: ..."
- กรณีจริงเมื่อ 11 พ.ย. 2569: อีเมลผู้รับผิดชอบใน roster พิมพ์ผิด Google ตอบ `HttpError 400 Invalid attendee email` งานนั้นส่งไม่ผ่าน อีก 4 งานผ่าน แก้อีเมลแล้วกด "ส่งงานที่ยังไม่ส่ง" ได้ (`ui/tab_calendar.py:257-260`)
- ถ้ายังไม่เชื่อม Calendar ตอนอนุมัติ: อนุมัติรายงานได้ แต่ยังไม่ส่งนัด ผู้ใช้เชื่อมแล้วกดส่งภายหลัง (`approve_and_send` คอมเมนต์ `service.py:752`, ข้อความเตือนใน `ui/tab_calendar.py:152`)

### 5.2 `create_event` (`integrations/calendar_sync.py:108-128`)
1. อ่านงานจาก DB; ถ้ามีรหัสนัดอยู่แล้ว คืน `unchanged` ไม่สร้างซ้ำ (`:115-119`)
2. หาอีเมลผู้รับผิดชอบ: `attendee_email` (`:24-32`) ใช้ `match_participant_name` จับคู่ชื่อที่ AI ให้กับรายชื่อผู้เข้าร่วม ต้องจับคู่ได้คนเดียวและคนนั้นมีอีเมล (ไม่เช่นนั้นไม่แนบผู้ร่วมนัด)
3. สร้างเนื้อนัด `build_event_body` (หัวข้อ 5.3) ถ้าไม่มีวัน → `ValueError` ก่อนจะแตะ Google (`:122`)
4. เรียก `service.events().insert(calendarId="primary", body=body, sendUpdates="all" if send_invites else "none")` (`:124`)
   - ปฏิทินเป้าหมายคือ `primary` ของผู้ใช้ที่เป็นเจ้าของ token (ผู้ที่กดส่ง)
   - `sendUpdates="all"` = ให้ Google ส่งอีเมลเชิญจริง, `"none"` = ไม่ส่งอีเมล (ทั่วไป) ค่าเริ่มต้นไม่ส่ง กันอีเมลหลุดไปหาคนจริงตอนทดลอง (คอมเมนต์ `:112-113`)
   - ผู้รับผิดชอบที่มีอีเมลถูกแนบเป็นผู้ร่วมนัด **ไม่ว่าจะติ๊กส่งอีเมลหรือไม่** (`:92-93`) ต่างกันแค่ว่า Google ส่งอีเมลเชิญหรือไม่
5. จดรหัสนัด ลิงก์ (`htmlLink`) และ `invite_sent` (จริงเมื่อขอส่งอีเมลและนัดนี้มีผู้รับเชิญ) ลง `action_items` (`:125-127`, `db.py:1555`) ช่องติ๊ก "ส่งอีเมลเชิญ" ในแท็บ ⑤ ใช้ค่านี้แสดงความจริงหลังอนุมัติ (`service.py:685` `invites_sent`)

### 5.3 `build_event_body` (`calendar_sync.py:35-94`) กติกาแปลงงานเป็นนัด

| กรณีของงาน | นัดที่ได้ | โค้ด |
|---|---|---|
| ไม่มีวันกำหนด | ไม่สร้าง (`ValueError` "ยังไม่มีกำหนดวัน") ไม่เดาเป็น "พรุ่งนี้" | `:44-45` |
| มีวันเดียว ไม่มีเวลา | **นัดเต็มวัน** (`start.date`) | `:81-84` |
| มี `due_date_end` | **นัดหลายวัน** เต็มวัน ถึงวันสิ้นสุด (วันสิ้นสุดอยู่ก่อนวันเริ่ม → `ValueError`) | `:49-56`, `:53` |
| เต็มวัน Google นับวันสิ้นสุดแบบ "ไม่รวมวันนั้น" | บวก 1 วันให้ `end.date` | `:84` |
| มีเวลาเริ่ม | **นัดช่วงเวลา** (`start.dateTime`, `timeZone = Asia/Bangkok` ค่าคงที่ `:12`) | `:64-65`, `:79-80` |
| มีเวลาเริ่มแต่ไม่มีเวลาจบ | ยาว 1 ชั่วโมง (`DEFAULT_DURATION` `:13`) | `:76-78` |
| เวลาจบไม่หลังเวลาเริ่ม (เช่น 23:30–00:30) และไม่มีวันสิ้นสุด | ถือว่าข้ามไปวันถัดไป | `:68-69` |
| เวลาจบไม่หลังเวลาเริ่มทั้งที่มีวันสิ้นสุด | `ValueError` | `:70-71` |
| ทุกกรณี | `summary` = ชื่องาน; `description` = "ผู้รับผิดชอบ: ..." + "กำหนด: ..." + "สร้างจาก MeetSync" | `:60-62`, `:86-91` |

ตัวอย่างจริงใน Proposal: ภาพที่ 14 (นัดเต็มวัน 13 พ.ย. 2569), ภาพที่ 15–16 (นัดช่วงเวลา 6 พ.ย. 13:00 ถึง 8 พ.ย. 16:00 ข้อความในนัด "ผู้รับผิดชอบ ... กำหนด ... สร้างจาก MeetSync")

---

## 6. การตรวจนัดกลับมาเทียบกับ Calendar (ผู้ใช้แก้นัดเอง)

โจทย์: หลังส่งแล้ว ผู้ใช้อาจเปลี่ยนชื่อ เลื่อนวัน/เวลา หรือลบนัดใน Calendar เอง MeetSync ต้องไม่ขัดกับความจริง

| ขั้น | เกิดอะไรขึ้น | โค้ด |
|---|---|---|
| ตรวจเมื่อไร | **ตรวจเองเมื่อเปิดหน้าแท็บ ⑤** (ไม่ถี่เกิน 60 วินาที `CHECK_EVERY_S`) หรือกดปุ่ม "ตรวจการเปลี่ยนแปลงใน Google Calendar" เป็นการ "ถาม Google เมื่อเปิดหน้า/กดปุ่ม" ไม่ใช่เรียลไทม์ (ไม่มี webhook) | `ui/tab_calendar.py:71`, `:84-100` |
| ใครตรวจได้ | **เฉพาะผู้อนุมัติ** (นัดอยู่ในปฏิทินของผู้อนุมัติ คนอื่นอ่านแทนไม่ได้) | `ui/tab_calendar.py:87`, `service.py:718-719` |
| อ่านนัดจริง | `fetch_event` ดึงนัดตามรหัส ถ้า 404/410 หรือสถานะ `cancelled` ถือว่าถูกลบ | `calendar_sync.py:150-159` |
| เทียบ | `compare_with_google` เทียบ **ชื่อนัด** กับ **วัน/เวลา** ได้ผล `same` / `changed` / `deleted` **ผู้รับผิดชอบไม่เทียบ** (Calendar เก็บเป็นอีเมลผู้ได้รับเชิญ คนละชนิดกับชื่อผู้รับผิดชอบ) | `calendar_sync.py:162-177` |
| แปลงเวลา | เวลาของนัดจริงแปลงเป็นเวลาไทย (UTC+7) ก่อนเทียบเสมอ ผู้ใช้อาจแก้จากอุปกรณ์ที่ตั้งเขตเวลาอื่น และฝั่ง MeetSync คำนวณ "นัดที่เราเคยส่ง" ด้วยกติกาเดียวกับ `build_event_body` ก่อนเทียบ (ไม่งั้นนัดข้ามเที่ยงคืนหรือที่เรากำหนดความยาว 1 ชั่วโมงจะถูกมองว่าเปลี่ยนทั้งที่ไม่มีใครแก้) | `calendar_sync.py:131-147` (`event_schedule`), `:174` |
| แสดงผล | ถ้าต่างกัน แสดงตารางเทียบ "ใน MeetSync" กับ "ใน Google Calendar" | `ui/tab_calendar.py:109-117` |
| ปรับตาม | **ต่อเมื่อผู้ใช้กดปุ่ม "อัปเดต MeetSync ตาม Google Calendar"** ไม่ปรับให้เอง | `ui/tab_calendar.py:118`, `service.py:738` `apply_calendar_changes` |
| ที่ปรับ | แก้เฉพาะ **ชื่อและวัน/เวลา** ของงาน (ทำได้แม้รายงานอนุมัติแล้ว ส่วนอื่นยังล็อก) เก็บเป็นฉบับใหม่ชนิด `calendar_sync` ในประวัติ | `db.py:1526-1552` |
| นัดถูกลบใน Google | ล้างรหัสนัด ลิงก์ และ `invite_sent` ของงานนั้น (งานไม่ถูกลบ) ส่งใหม่ได้ด้วยปุ่ม "ส่งงานที่ยังไม่ส่ง" | `db.py:1546-1549` |

---

## 7. การลบนัด: ยกเลิกอนุมัติและลบการประชุม

- **ยกเลิกอนุมัติ** (`service.py:611` `reopen_report`): ลบนัดที่ส่งไปแล้วออกจาก Calendar ก่อน (`_delete_calendar_events` `:626`) แล้วค่อยปลดล็อกรายงาน (`db.reopen_report`)
- **ลบการประชุมที่อนุมัติแล้ว** (`service.py:236` `delete_meeting`): ลบนัดด้วย ก่อนลบข้อมูล
- กติกาของ `_delete_calendar_events` (`:626-646`):
  - **เฉพาะผู้อนุมัติ** ลบนัดได้ (นัดอยู่ในปฏิทินของเขา) ถ้าคนอื่นกด จะขึ้นข้อความบอกให้ผู้อนุมัติทำ (`:633-634`)
  - ลบสำเร็จหรือนัดไม่อยู่แล้ว (404/410) ไม่ถือเป็นข้อผิดพลาด (`calendar_sync.py:180-189`) ลบด้วย `sendUpdates="none"` ไม่ส่งอีเมลยกเลิก
  - ถ้าลบไม่สำเร็จ (เช่น ยังไม่ได้เชื่อม Calendar) และไม่ได้ติ๊กยืนยัน → **ไม่ยกเลิกอนุมัติ/ไม่ลบการประชุมให้** เพื่อไม่ให้ทิ้งนัดค้าง ถ้าติ๊กยืนยัน (`force`) ยอมให้นัดค้างแล้วลบเอง (`:639-640`)
- เทสต์ที่ตรงกับพฤติกรรมนี้อยู่ในหัวข้อ 9

---

## 8. สิ่งที่ต้องตั้งค่าและข้อจำกัดที่ต้องบอกอาจารย์ตรงๆ

**ที่ต้องตั้งค่าฝั่ง Google (README หัวข้อ "ติดตั้งและรัน"):** เปิดใช้ Calendar API, ตั้งหน้า OAuth consent, สร้าง OAuth Client ประเภท Web, เพิ่ม redirect URI สองตัว (`http://localhost:8501/oauth2callback` สำหรับล็อกอิน และ `http://localhost:8000/auth/google/callback` สำหรับ Calendar) ใส่ค่า `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` ใน `.env` ตามที่ทีมระบุไว้ในบทพูดทดสอบ ถ้าโปรเจกต์ Google ยังอยู่ในโหมดทดสอบ ต้องเพิ่มอีเมลอาจารย์และผู้ทดสอบทุกคนในรายชื่อผู้ทดสอบ (ผมไม่ได้ตรวจหน้าตั้งค่าของ Google จึงยืนยันรายละเอียดข้อนี้ไม่ได้)

**ข้อจำกัด:**
1. **token เก็บใน MySQL แบบไม่เข้ารหัส** (`users.calendar_token`) เหมาะกับต้นแบบเครื่องเดียว ถ้านำไปใช้จริงต้องเข้ารหัสก่อนเก็บ
2. **ไม่ใช่เรียลไทม์:** การตรวจนัดกลับถามเมื่อเปิดหน้าหรือกดปุ่มเท่านั้น
3. **ไม่ซิงก์ผู้รับผิดชอบ:** เทียบแค่ชื่อและวัน/เวลา
4. **อีเมลผู้รับผิดชอบผิดรูปแบบ** ทำให้ Google ปฏิเสธนัดของงานนั้น (งานอื่นยังส่งได้) ระบบยังไม่ข้ามอีเมลผิดให้เอง
5. **การตรวจและลบนัดทำได้เฉพาะผู้อนุมัติ** และถ้าประธาน/เลขากดส่งซ้ำ นัดจะเข้าปฏิทินของผู้ที่กดส่งซ้ำ (ซึ่งจะไม่ใช่ปฏิทินของผู้อนุมัติ และผู้อนุมัติตรวจ/ลบนัดนั้นไม่ได้) เป็นกรณีมุมของการออกแบบ (`service.py:702` ใช้ token ของผู้ที่กดส่ง)
6. **ส่งอีเมลเชิญจริงยังไม่ได้ยืนยันกับ Google จริง** (ทดสอบกับตัวจำลอง) การสร้างนัดหลายงานกับ Google จริงทำได้แล้ว (6 พ.ย. 2569 สำเร็จ 4 จาก 5 งาน) ตาม README
7. การลบนัดตอนยกเลิกอนุมัติ ผู้พัฒนาแจ้งว่าทดลองกับ Google จริงแล้ว (ตาม README ไม่มีบันทึกผลเป็นลายลักษณ์อักษร)
8. คอมเมนต์หัวไฟล์ `calendar_auth.py:9-11` ล้าสมัย (อ้างตาราง `calendar_tokens` ที่ไม่มีแล้ว)

---

## 9. เทสต์ที่รองรับ (ใน `tests/` รันกับตัวจำลองของ Google ไม่เรียก Google จริง)

| ไฟล์ | เทสต์ (ตัวอย่าง) |
|---|---|
| `test_calendar.py` (23 ข้อ) | `ConnectLinkTests` (4 ข้อ ลิงก์เชื่อมและ `login_hint`), `BuildEventTests` (11 ข้อ) เช่น `test_all_day_end_is_exclusive_in_google_and_inclusive_in_meetsync`, `test_multi_day_timed_event_has_an_end_date`, `test_late_start_rolls_over_midnight_instead_of_ending_before_it_starts`, `EventScheduleTests` (6), `AttendeeEmailTests` (2) |
| `test_service.py` | `test_approve_and_send_creates_events_for_dated_items_with_attendees_and_stores...`, `test_unchanged_events_are_reported_as_the_same`, `test_attendees_changed_in_google_do_not_change_the_assignee`, `test_event_deleted_in_google_is_flagged_and_its_link_cleared_on_apply_but_the_task_stays`, `test_reopen_deletes_sent_events_and_clears_marks_then_approval_sends_again`, `test_reopen_is_blocked_when_events_cannot_be_deleted_unless_forced`, `test_event_already_deleted_in_google_is_not_an_error_on_reopen`, `test_delete_approved_meeting_removes_calendar_events_or_refuses`, `test_only_the_approver_can_delete_their_calendar_events` |
| `test_bot_service.py` | `test_calendar_connect_requires_a_valid_signed_token`, `test_calendar_callback_saves_token_for_the_signed_user_and_returns_only_to_our_ui` |

---

## 10. ตารางสรุป: สิ่งที่ระบบทำ → โค้ดที่ทำ

| สิ่งที่ทำ | ฟังก์ชัน | ไฟล์:บรรทัด |
|---|---|---|
| แสดงสถานะ/ปุ่มเชื่อม Calendar | `_calendar_chip`, `calendar_connected`, `is_connected` | `ui/common.py:196`, `service.py:681`, `calendar_auth.py:64` |
| สร้างโทเคนที่เซ็นแล้วและลิงก์เชื่อม | `calendar_connect_token`, `calendar_connect_url`, `read_calendar_token` | `bot/botclient.py:49`, `:62`, `:58` |
| รับคำขอเชื่อมและ callback | `calendar_connect`, `calendar_callback`, `_safe_return` | `bot_service.py:348`, `:357`, `:343` |
| สร้างลิงก์ขออนุญาตและแลก token | `build_auth_url`, `exchange_code` | `calendar_auth.py:48`, `:58` |
| เก็บ/ล้าง/อ่าน token | `save_calendar_token`, `clear_calendar_token`, `get_calendar_token` | `db.py:343`, `:352`, `:362` |
| ต่ออายุ token | `get_credentials` | `calendar_auth.py:76` |
| อนุมัติแล้วส่งนัด | `approve_and_send`, `approve_report`, `sync_calendar` | `service.py:751`, `:593`, `:690` |
| สร้างนัดทีละงาน | `create_event`, `attendee_email`, `build_event_body` | `calendar_sync.py:108`, `:24`, `:35` |
| จดสถานะนัด | `mark_action_item_synced` | `db.py:1555` |
| ตรวจนัดกลับ | `check_calendar_changes`, `fetch_event`, `compare_with_google`, `event_schedule` | `service.py:711`, `calendar_sync.py:150`, `:162`, `:131` |
| ปรับงานตาม Calendar | `apply_calendar_changes` | `service.py:738`, `db.py:1526` |
| ลบนัด | `_delete_calendar_events`, `delete_event` | `service.py:626`, `calendar_sync.py:180` |
| หน้าจอแท็บ ⑤ | `_approve_dialog`, `_calendar_changes`, ปุ่ม "ส่งงานที่ยังไม่ส่ง" | `ui/tab_calendar.py:137`, `:84`, `:257` |

---

## 11. ถ้าอาจารย์ถาม

| คำถาม | คำตอบ |
|---|---|
| ระบบเข้าถึงปฏิทินของผู้ใช้ได้อย่างไร | ผู้ใช้กดอนุญาตผ่าน OAuth 2.0 ของ Google เองที่ปุ่มเชื่อมต่อ ระบบได้ token ไม่ได้รับรหัสผ่าน ขอสิทธิ์ `calendar.events` เท่านั้น (สร้าง/แก้/ลบกิจกรรม ไม่ใช่จัดการปฏิทินทั้งหมด) |
| ทำไมแยกสิทธิ์ล็อกอินกับ Calendar | `st.login` ไม่ส่ง access token ของ Google ให้แอป และให้ผู้ใช้เห็นว่าแต่ละขั้นขออะไร ล็อกอินขอแค่ระบุตัวตน |
| token เก็บที่ไหน ปลอดภัยไหม | เก็บใน MySQL คอลัมน์ `users.calendar_token` แยกรายผู้ใช้ ไม่เข้ารหัส เป็นข้อจำกัดของต้นแบบ ถ้านำไปใช้จริงต้องเข้ารหัส |
| token หมดอายุทำอย่างไร | ต่ออายุอัตโนมัติด้วย refresh token ถ้า Google ปฏิเสธ (หมดอายุจริงหรือถูกเพิกถอน) ระบบล้าง token และให้กดเชื่อมใหม่ที่ปุ่มเดิม |
| นัดเข้าปฏิทินของใคร | ของผู้ที่กดส่ง (ปกติคือผู้อนุมัติ ตอนกด "อนุมัติและส่ง") |
| ส่งนัดงานเดียวล้มเหลวเกิดอะไร | งานนั้นขึ้นว่าส่งไม่สำเร็จ งานอื่นส่งต่อ ไม่ย้อนการอนุมัติ กดส่งซ้ำเฉพาะที่ค้างได้ |
| ส่งอีเมลเชิญไหม | ผู้รับผิดชอบที่มีอีเมลในรายชื่อถูกเพิ่มเป็นผู้ร่วมนัดเสมอ แต่ Google ส่งอีเมลเชิญจริงเมื่อผู้ใช้ติ๊กช่อง "ส่งอีเมลเชิญ" เท่านั้น (ค่าเริ่มต้นไม่ส่ง) |
| ถ้าผู้ใช้แก้นัดใน Calendar เอง | ระบบตรวจเมื่อเปิดหน้าหรือกดปุ่ม แสดงตารางเทียบ แล้วปรับงานตามเมื่อผู้ใช้กดยืนยัน (ชื่อและวัน/เวลาเท่านั้น ไม่ใช่เรียลไทม์ ไม่ซิงก์ผู้รับผิดชอบ) |
| ยกเลิกอนุมัติแล้วนัดล่ะ | ลบนัดที่ส่งไปแล้วออกจาก Calendar ก่อน (ผู้อนุมัติเท่านั้น) ถ้าลบไม่ได้จะไม่ยกเลิกให้ เว้นแต่ติ๊กยืนยัน |
| รองรับนัดแบบไหน | วันเดียว หลายวัน และช่วงเวลา (เวลาเริ่ม-สิ้นสุด) เขตเวลา Asia/Bangkok ไม่ระบุเวลาจบจะยาว 1 ชั่วโมง |


## ผลตรวจเพิ่ม (12 พ.ย. 2569, อ่านโค้ดอย่างเดียว ยังไม่ได้รันทดสอบ)

- **การเชิญไม่กรองตามสถานะผู้เข้าร่วม** ผู้ถูกเพิ่มเป็นผู้ร่วมนัดคือผู้รับผิดชอบของงานนั้นเท่านั้น (จับคู่ชื่อได้คนเดียวและมีอีเมล) ไม่ดูว่าสถานะเป็นเข้าร่วม ไม่มา หรือยังไม่ยืนยัน (`calendar_sync.py:24-32`, `:121`)
- **ส่งซ้ำเฉพาะงานที่ล้มเหลวได้** `sync_calendar` ข้ามงานที่มีรหัสนัดแล้ว และข้ามงานที่ไม่มีกำหนดวัน งานที่ล้มจึงถูกลองใหม่โดยไม่สร้างนัดซ้ำ (`service.py:698`, `calendar_sync.py:118-119`) กรณีกดส่งพร้อมกันสองหน้าต่างผมไม่ได้ตรวจ
- **ช่องส่งอีเมลเชิญ** ค่าเริ่มต้นมาจากตัวแปร `CALENDAR_SEND_INVITES` ถ้าไม่ตั้งเป็น `1` ช่องจะไม่ถูกติ๊ก (`ui/tab_calendar.py:55-56`, `:212`) ผมไม่ได้เปิด `.env` จึงไม่ทราบค่าจริง
- **อายุโทเคนในโหมดทดสอบของ Google** ยังไม่ได้ตรวจ ต้องดูในหน้าตั้งค่า Google Cloud
