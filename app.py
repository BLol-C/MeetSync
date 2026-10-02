"""
เว็บแอปโลคอลสำหรับ MeetSync — ตั้งค่าการประชุม สั่งบอทจับคำบรรยายจาก Google Meet ตรวจทาน transcript
ให้ AI ร่างรายงานการประชุม แล้วให้คนตรวจ/แก้/อนุมัติ

รัน:  venv\\Scripts\\python.exe -m uvicorn app:app --port 8000
เปิด: http://localhost:8000
"""

import asyncio
import contextlib
import datetime
import pathlib
import secrets
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()  # ต้องโหลดก่อน import db เพราะ db.py อ่าน env ตอน import (module level)

import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import auth
import calendar_auth
import calendar_sync
import db
import pdf_report
import report_data
import summarizer
from meet_engine import MEET_URL_RE, MeetCaptionEngine

HERE = pathlib.Path(__file__).parent
INDEX_HTML = (HERE / "frontend.html").read_text(encoding="utf-8")
MEETING_HTML = (HERE / "meeting.html").read_text(encoding="utf-8")

LOGIN_HTML = """
<!doctype html><html lang="th"><head><meta charset="utf-8">
<title>เข้าสู่ระบบ — MeetSync</title>
<style>
body{margin:0;background:#0f1115;color:#e6e8ec;font:15px/1.55 "Segoe UI",Tahoma,system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;height:100vh}
.box{background:#171a21;border:1px solid #2a2f3a;border-radius:12px;padding:32px 40px;text-align:center}
h1{font-size:18px;margin:0 0 18px}
a.btn{display:inline-block;background:#4c8dff;color:#fff;text-decoration:none;font-weight:600;
  padding:10px 20px;border-radius:8px}
a.btn:hover{background:#3a6fd8}
</style></head><body>
<div class="box"><h1>MeetSync</h1><a class="btn" href="/auth/login">เข้าสู่ระบบด้วย Google</a></div>
</body></html>
"""

_QUEUE_MAX = 2000      # event ที่ค้างในคิวของ WebSocket แต่ละ client ได้สูงสุด (กัน client ค้างกินหน่วยความจำ)
_ROW_MAP_MAX = 500     # จำนวนแถวคำบรรยายล่าสุดที่จำ segment_id ไว้ (แถวเก่ากว่านี้ไม่กลับมา final ซ้ำแล้ว)


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _db_ready
    try:
        db.init_schema()
        _db_ready = True
    except Exception as e:  # noqa: BLE001 — DB ต่อไม่ได้ก็ให้แอปทำงานต่อแบบเดิมได้ (แค่ไม่บันทึกลง DB)
        _db_ready = False
        _broadcast({"type": "status", "text": f"⚠️ เชื่อมต่อฐานข้อมูลไม่สำเร็จ (จะไม่บันทึกลง DB): {e!r}"})
    yield
    await _finalize()


app = FastAPI(lifespan=_lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET"])

_queues: dict[asyncio.Queue, int | None] = {}   # คิวของแต่ละ WebSocket -> user_id เจ้าของ connection
_history: list[dict] = []          # status + caption(final) ล่าสุด สำหรับ client ที่เพิ่งต่อ
_engine: MeetCaptionEngine | None = None
_db_ready = False
_current_meeting_id: int | None = None
_run_owner_id: int | None = None   # user_id ของคนที่สั่งบอทรันอยู่ — เห็น caption สด/สั่งหยุดได้เฉพาะคนนี้
_seq_no = 0
_segment_by_row: OrderedDict[int, int] = OrderedDict()   # id แถวคำบรรยาย (จาก JS) -> segment_id ใน DB
                                                          # กันไม่ให้คนพูดยาวคนเดียวถูกบันทึกเป็นหลายแถวซ้ำ ๆ
_save_queue: asyncio.Queue | None = None    # คิวเขียน segment ลง DB (ประมวลผลทีละอัน ตามลำดับ)
_save_worker: asyncio.Task | None = None
_finalize_lock = asyncio.Lock()


# ── บันทึก transcript ลง DB (นอก event loop) ──

def _persist_segment(meeting_id: int, row_id: int | None, name: str, text: str):
    """เขียน segment ลง MySQL — รันใน thread (PyMySQL เป็น sync ห้ามเรียกบน event loop)

    แถวคำบรรยายเดียวกัน (row_id) อาจ final ซ้ำหลายครั้งระหว่างที่คนคนเดิมพูดต่อ
    (เช่น หยุดหายใจ/เว้นจังหวะเกิน 1.5 วิ) — ครั้งแรก insert แถวใหม่ ครั้งต่อ ๆ ไป update
    แถวเดิมด้วยข้อความที่ยาวขึ้น แทนที่จะ insert ซ้ำ

    เรียกจาก worker ตัวเดียวเท่านั้น จึงแตะ _seq_no/_segment_by_row ได้โดยไม่ต้องล็อก
    """
    global _seq_no
    segment_id = _segment_by_row.get(row_id)
    if segment_id is not None:
        db.update_segment(segment_id, text)
        return
    speaker_id = db.get_or_create_speaker(meeting_id, name)
    _seq_no += 1
    segment_id = db.insert_segment(meeting_id, speaker_id, _seq_no, text)
    if row_id is not None:
        _segment_by_row[row_id] = segment_id
        while len(_segment_by_row) > _ROW_MAP_MAX:
            _segment_by_row.popitem(last=False)


async def _save_worker_loop(q: asyncio.Queue):
    while True:
        item = await q.get()
        try:
            await asyncio.to_thread(_persist_segment, *item)
        except Exception as e:  # noqa: BLE001 — เขียน DB พลาดต้องไม่ทำให้ worker/เว็บล่ม
            _broadcast({"type": "status", "text": f"⚠️ บันทึกลง DB ไม่สำเร็จ: {e!r}"})
        finally:
            q.task_done()


def _save_segment(ev: dict):
    """ส่งช่วงคำพูดที่ final แล้วเข้าคิวเขียน DB (ไม่บล็อก event loop)"""
    if not _db_ready or _current_meeting_id is None or _save_queue is None:
        return
    name = ev.get("name")
    text = (ev.get("text") or "").strip()
    if not name or name == "(raw)" or not text:
        return
    _save_queue.put_nowait((_current_meeting_id, ev.get("id"), name, text))


# ── ส่ง event ไปหน้าเว็บ ──

def _offer(q: asyncio.Queue, ev: dict):
    """ใส่ event เข้าคิวของ client — ถ้าคิวเต็ม (client ช้า/ค้าง) ห้ามให้โตไม่จำกัด"""
    try:
        q.put_nowait(ev)
        return
    except asyncio.QueueFull:
        pass
    if ev.get("type") == "caption" and not ev.get("final"):
        return  # ภาพสดที่ยังไม่ final ทิ้งได้ ข้อความจริงจะมาตอน final อีกครั้ง
    for _ in range(max(q.maxsize // 4, 1)):  # event สำคัญ: ทิ้งของเก่าที่สุดบางส่วนเพื่อเปิดที่ให้
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break
    q.put_nowait(ev)


def _broadcast(ev: dict):
    if ev.get("type") == "status" or (ev.get("type") == "caption" and ev.get("final")):
        _history.append(ev)
        if len(_history) > 1500:
            del _history[:750]
    if ev.get("type") == "caption" and ev.get("final"):
        _save_segment(ev)
    for q, uid in list(_queues.items()):
        if _run_owner_id is None or uid == _run_owner_id:
            _offer(q, ev)


def _send_to_user(user_id: int | None, ev: dict):
    """ส่ง event ให้ทุก connection ของผู้ใช้คนนี้เท่านั้น (ไม่ผ่าน history)"""
    for q, uid in list(_queues.items()):
        if uid == user_id:
            _offer(q, ev)


def _on_engine_event(engine: MeetCaptionEngine, ev: dict):
    if engine is not _engine:
        return  # event ตกค้างจากบอทตัวเก่าที่ถูกแทนไปแล้ว
    if ev.get("type") == "ended":
        # บอทจบเอง (ประชุมจบ/เบราว์เซอร์ปิด/error) -> ปิดประชุมใน DB และแจ้งหน้าเว็บให้เรียบร้อย
        asyncio.get_running_loop().create_task(_finalize(only_if_engine=engine))
        return
    _broadcast(ev)


# ── เริ่ม/หยุดบอท ──

def _bot_busy_with_other(user_id: int | None) -> bool:
    return bool(_engine and _engine.is_running() and _run_owner_id != user_id)


async def _finalize(only_if_engine: MeetCaptionEngine | None = None):
    """หยุดบอท รอเขียน transcript ที่ค้างคิวให้ครบ แล้วปิดประชุมใน DB — เรียกซ้ำได้โดยไม่เสียหาย"""
    global _engine, _current_meeting_id, _save_queue, _save_worker
    async with _finalize_lock:
        if only_if_engine is not None and _engine is not only_if_engine:
            return  # มีคนสั่งหยุด/เริ่มใหม่ไปก่อนแล้ว — อย่าไปปิดการประชุมรอบใหม่
        if _engine is None and _current_meeting_id is None and _save_worker is None:
            return
        if _engine:
            await _engine.stop()
            _engine = None
        if _save_queue is not None:
            await _save_queue.join()
        if _save_worker is not None:
            _save_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _save_worker
        _save_queue = None
        _save_worker = None

        ended_meeting_id = _current_meeting_id
        if _db_ready and ended_meeting_id is not None:
            try:
                await asyncio.to_thread(db.end_meeting, ended_meeting_id)
            except Exception as e:  # noqa: BLE001
                _broadcast({"type": "status", "text": f"⚠️ ปิดบันทึกการประชุมใน DB ไม่สำเร็จ: {e!r}"})
        _current_meeting_id = None
        _broadcast({"type": "state", "running": False})
        if ended_meeting_id is not None:
            _broadcast({"type": "meeting_ended", "meeting_id": ended_meeting_id})


async def _start(url: str, user_id: int | None, email: str | None, meeting_id: int | None = None):
    """เริ่มบอท — ถ้าระบุ meeting_id จะใช้การประชุมที่ตั้งค่าไว้ (หรือบันทึกต่อจากเดิมหลังบอทหลุด)
    ถ้าไม่ระบุจะสร้างการประชุมเปล่าจากลิงก์ที่วางมา (วิธีเร็ว ไม่มีรายชื่อผู้เข้าร่วม)
    """
    global _engine, _current_meeting_id, _seq_no, _run_owner_id, _save_queue, _save_worker
    if _bot_busy_with_other(user_id):
        _send_to_user(user_id, {"type": "error", "text": "บอทกำลังถูกใช้งานโดยผู้ใช้อื่น — รอให้การประชุมนั้นจบก่อน"})
        return

    if meeting_id is not None:
        if not _db_ready:
            _send_to_user(user_id, {"type": "error", "text": "ฐานข้อมูลไม่พร้อม เริ่มการประชุมที่ตั้งค่าไว้ไม่ได้"})
            return
        meeting = await asyncio.to_thread(db.get_meeting, meeting_id)
        if not meeting or not await asyncio.to_thread(db.is_meeting_manager, meeting, user_id, email):
            _send_to_user(user_id, {"type": "error", "text": "ไม่พบการประชุมนี้"})
            return
        url = meeting["meet_url"]
    if not MEET_URL_RE.match(url):
        _send_to_user(user_id, {"type": "error", "text": "ลิงก์ไม่ถูกต้อง (ต้องเป็น https://meet.google.com/xxx-xxxx-xxx)"})
        return

    await _finalize()  # ปิดการประชุมก่อนหน้า (ถ้ามี) ให้เรียบร้อยก่อนเริ่มรอบใหม่

    new_meeting_id: int | None = None
    start_seq = 0
    if _db_ready:
        try:
            if meeting_id is not None:
                if not await asyncio.to_thread(db.begin_recording, meeting_id):
                    _send_to_user(user_id, {
                        "type": "error",
                        "text": "เริ่มบันทึกการประชุมนี้ไม่ได้ (รายงานผ่านขั้นตรวจทานไปแล้ว)",
                    })
                    return
                new_meeting_id = meeting_id
                start_seq = await asyncio.to_thread(db.max_sequence_no, meeting_id)
            else:
                new_meeting_id = await asyncio.to_thread(db.create_meeting, url, user_id)
        except Exception as e:  # noqa: BLE001
            _broadcast({"type": "status", "text": f"⚠️ สร้างบันทึกการประชุมใน DB ไม่สำเร็จ: {e!r}"})

    _run_owner_id = user_id
    _history.clear()
    _broadcast({"type": "cleared"})
    _broadcast({"type": "state", "running": True})

    _current_meeting_id = new_meeting_id
    _seq_no = start_seq
    _segment_by_row.clear()
    if new_meeting_id is not None:
        _save_queue = asyncio.Queue()
        _save_worker = asyncio.create_task(_save_worker_loop(_save_queue))

    engine: MeetCaptionEngine = MeetCaptionEngine(on_event=lambda ev: _on_engine_event(engine, ev))
    _engine = engine
    engine.start(url)


async def _stop(user_id: int | None):
    if _bot_busy_with_other(user_id):
        _send_to_user(user_id, {"type": "error", "text": "บอทนี้ไม่ใช่ของคุณ — หยุดไม่ได้"})
        return
    await _finalize()


# ── session / สิทธิ์ ──

def _session_user(request: Request | WebSocket) -> dict | None:
    """user ใน session ถ้าใช้ได้ — session เก่าที่ไม่มี user_id (ล็อกอินก่อนมีระบบเจ้าของ) ต้องล็อกอินใหม่"""
    user = request.session.get("user")
    if not user or (_db_ready and not user.get("user_id")):
        return None
    return user


def _require_user(request: Request) -> dict:
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="ยังไม่ได้เข้าสู่ระบบ")
    return user


async def _managed_meeting(meeting_id: int, user: dict) -> dict:
    """ดึงการประชุมที่ผู้ใช้จัดการได้ (เจ้าของ หรือประธาน/เลขาตามอีเมล) — ไม่พบหรือไม่มีสิทธิ์ตอบ 404 เหมือนกัน
    (ไม่เปิดเผยว่าการประชุมนั้นมีอยู่จริง)
    """
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    meeting = await asyncio.to_thread(db.get_meeting, meeting_id)
    if not meeting or not await asyncio.to_thread(
        db.is_meeting_manager, meeting, user.get("user_id"), user.get("email")
    ):
        raise HTTPException(status_code=404, detail="ไม่พบการประชุมนี้")
    return meeting


def _require_status(meeting: dict, *allowed: str, hint: str = ""):
    if meeting["status"] not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"ทำรายการนี้ไม่ได้ในสถานะ \"{meeting['status']}\"" + (f" — {hint}" if hint else ""),
        )


def _value_error(e: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ── หน้าเว็บ + login ──

@app.get("/")
async def index(request: Request) -> HTMLResponse:
    if not _session_user(request):
        return RedirectResponse("/login")
    return HTMLResponse(INDEX_HTML)


@app.get("/meeting/{ident}")
async def meeting_page(ident: str, request: Request) -> HTMLResponse:
    """หน้าจัดการการประชุม (ident = เลข meeting_id หรือคำว่า new) — JS ในหน้าอ่านเลขจาก URL เอง"""
    if not _session_user(request):
        return RedirectResponse("/login")
    return HTMLResponse(MEETING_HTML)


@app.get("/login")
async def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML)


@app.get("/auth/login")
async def auth_login(request: Request):
    state = secrets.token_urlsafe(32)
    request.session["login_state"] = state
    return RedirectResponse(auth.build_login_url(state))


@app.get("/auth/login/callback")
async def auth_login_callback(request: Request, code: str | None = None, state: str | None = None):
    expected = request.session.pop("login_state", None)
    if not code or not expected or not state or not secrets.compare_digest(state, expected):
        raise HTTPException(status_code=400, detail="การเข้าสู่ระบบไม่ถูกต้องหรือหมดอายุ — ลองใหม่อีกครั้ง")
    info = await asyncio.to_thread(auth.exchange_login_code, code)
    user_id = None
    if _db_ready:
        user_id = await asyncio.to_thread(
            db.upsert_user, info["google_sub"], info["email"], info["name"], info["picture"]
        )
    request.session["user"] = {
        "user_id": user_id,
        "email": info["email"],
        "name": info["name"],
        "picture": info["picture"],
    }
    return RedirectResponse("/")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/me")
async def me(request: Request) -> dict:
    return _require_user(request)


# ── การประชุม: ตั้งค่า / รายละเอียด ──

class ParticipantIn(BaseModel):
    display_name: str
    email: str | None = None
    role: str = "attendee"
    attendance: str = "invited"


class MeetingSetupIn(BaseModel):
    meet_url: str
    title: str | None = None
    venue: str | None = None
    scheduled_at: datetime.datetime | None = None
    meeting_no: str | None = None
    org_name: str | None = None
    participants: list[ParticipantIn] = []


class MeetingPatchIn(BaseModel):
    meet_url: str | None = None
    title: str | None = None
    venue: str | None = None
    scheduled_at: datetime.datetime | None = None
    meeting_no: str | None = None
    org_name: str | None = None


class ParticipantPatchIn(BaseModel):
    display_name: str | None = None
    email: str | None = None
    role: str | None = None
    attendance: str | None = None


def _check_meet_url(url: str) -> str:
    url = (url or "").strip()
    if not MEET_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="ลิงก์ไม่ถูกต้อง (ต้องเป็น https://meet.google.com/xxx-xxxx-xxx)")
    return url


@app.get("/meetings")
async def list_meetings(request: Request) -> list[dict]:
    user = _require_user(request)
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    return await asyncio.to_thread(db.list_meetings, user["user_id"], user.get("email"))


@app.post("/meetings")
async def create_meeting(body: MeetingSetupIn, request: Request) -> dict:
    """สร้างการประชุมพร้อมรายชื่อผู้เข้าร่วมและบทบาท (ยังไม่สั่งบอท — สั่งผ่านปุ่มเริ่มบอทแยกต่างหาก)"""
    user = _require_user(request)
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    url = _check_meet_url(body.meet_url)
    try:
        # ตรวจรายชื่อ+บทบาทให้ผ่านทั้งหมดก่อนเขียน DB จะได้ไม่เหลือประชุมครึ่งๆ กลางๆ ถ้ารายการท้ายผิด
        roles = [p.role for p in body.participants]
        for r in ("chair", "secretary"):
            if roles.count(r) > 1:
                raise ValueError("ประธานและเลขามีได้อย่างละ 1 คนต่อการประชุม")
        for p in body.participants:
            if not p.display_name.strip():
                raise ValueError("ต้องระบุชื่อผู้เข้าร่วมทุกคน")
            db._check_participant_fields(p.role, p.attendance)
        meeting_id = await asyncio.to_thread(
            db.create_meeting_setup, user["user_id"],
            meet_url=url, title=body.title, venue=body.venue, scheduled_at=body.scheduled_at,
            meeting_no=body.meeting_no, org_name=body.org_name,
        )
        for p in body.participants:
            await asyncio.to_thread(
                db.add_participant, meeting_id, p.display_name, p.email, p.role, p.attendance
            )
    except ValueError as e:
        raise _value_error(e)
    return {"meeting_id": meeting_id}


async def _meeting_detail(meeting: dict, user: dict) -> dict:
    mid = meeting["meeting_id"]
    participants = await asyncio.to_thread(db.list_participants, mid)
    speakers = await asyncio.to_thread(db.list_speakers, mid)
    header = report_data.build_header(meeting, participants)
    return {
        "meeting": meeting,
        "participants": participants,
        "speakers": speakers,
        "header": header,
        "header_warnings": report_data.header_warnings(header),
        "bot_running": bool(_engine and _engine.is_running() and _current_meeting_id == mid),
        "calendar_connected": bool(await asyncio.to_thread(calendar_auth.is_connected, user["user_id"])),
        "is_owner": meeting["owner_user_id"] == user["user_id"],
    }


@app.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: int, request: Request) -> dict:
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    return await _meeting_detail(meeting, user)


@app.patch("/meetings/{meeting_id}")
async def patch_meeting(meeting_id: int, body: MeetingPatchIn, request: Request) -> dict:
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "scheduled", "recording", "transcript_review", "transcript_verified", "draft",
                    hint="รายงานที่อนุมัติแล้วแก้ข้อมูลการประชุมไม่ได้")
    fields = body.model_dump(exclude_unset=True)
    if "meet_url" in fields:
        if meeting["status"] != "scheduled":
            raise HTTPException(status_code=409, detail="เปลี่ยนลิงก์ได้เฉพาะก่อนเริ่มบอท")
        fields["meet_url"] = _check_meet_url(fields["meet_url"])
    try:
        await asyncio.to_thread(db.update_meeting_setup, meeting_id, **fields)
    except ValueError as e:
        raise _value_error(e)
    return await _meeting_detail(await asyncio.to_thread(db.get_meeting, meeting_id), user)


# ── ผู้เข้าร่วม / บทบาท / จับคู่ผู้พูด ──

async def _participant_meeting(participant_id: int, user: dict) -> tuple[dict, dict]:
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    p = await asyncio.to_thread(db.get_participant, participant_id)
    if not p:
        raise HTTPException(status_code=404, detail="ไม่พบผู้เข้าร่วมนี้")
    return p, await _managed_meeting(p["meeting_id"], user)


@app.post("/meetings/{meeting_id}/participants")
async def add_participant(meeting_id: int, body: ParticipantIn, request: Request) -> dict:
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "scheduled", "recording", "transcript_review", "transcript_verified", "draft",
                    hint="รายงานที่อนุมัติแล้วแก้รายชื่อไม่ได้")
    try:
        pid = await asyncio.to_thread(
            db.add_participant, meeting_id, body.display_name, body.email, body.role, body.attendance
        )
        await asyncio.to_thread(db.auto_link_speakers, meeting_id)
    except ValueError as e:
        raise _value_error(e)
    return {"participant_id": pid}


@app.patch("/participants/{participant_id}")
async def patch_participant(participant_id: int, body: ParticipantPatchIn, request: Request) -> dict:
    user = _require_user(request)
    _, meeting = await _participant_meeting(participant_id, user)
    _require_status(meeting, "scheduled", "recording", "transcript_review", "transcript_verified", "draft",
                    hint="รายงานที่อนุมัติแล้วแก้รายชื่อไม่ได้")
    try:
        await asyncio.to_thread(db.update_participant, participant_id, **body.model_dump(exclude_unset=True))
        if body.display_name is not None:
            await asyncio.to_thread(db.auto_link_speakers, meeting["meeting_id"])
    except ValueError as e:
        raise _value_error(e)
    return await asyncio.to_thread(db.get_participant, participant_id)


@app.delete("/participants/{participant_id}")
async def delete_participant(participant_id: int, request: Request) -> dict:
    user = _require_user(request)
    _, meeting = await _participant_meeting(participant_id, user)
    _require_status(meeting, "scheduled", "recording", "transcript_review", "transcript_verified", "draft",
                    hint="รายงานที่อนุมัติแล้วแก้รายชื่อไม่ได้")
    await asyncio.to_thread(db.delete_participant, participant_id)
    return {"ok": True}


class SpeakerLinkIn(BaseModel):
    participant_id: int | None


@app.put("/meetings/{meeting_id}/speakers/{speaker_id}/participant")
async def link_speaker(meeting_id: int, speaker_id: int, body: SpeakerLinkIn, request: Request) -> dict:
    """จับคู่ชื่อที่ปรากฏใน Meet (ผู้พูด) กับผู้เข้าร่วมที่ลงทะเบียนไว้ หรือยกเลิกด้วย participant_id = null"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "recording", "transcript_review", "transcript_verified", "draft")
    speakers = await asyncio.to_thread(db.list_speakers, meeting_id)
    if not any(s["speaker_id"] == speaker_id for s in speakers):
        raise HTTPException(status_code=404, detail="ไม่พบผู้พูดนี้ในการประชุมนี้")
    try:
        await asyncio.to_thread(db.link_speaker, speaker_id, body.participant_id)
    except ValueError as e:
        raise _value_error(e)
    return await _meeting_detail(await asyncio.to_thread(db.get_meeting, meeting_id), user)


@app.post("/meetings/{meeting_id}/auto-link")
async def auto_link(meeting_id: int, request: Request) -> dict:
    user = _require_user(request)
    await _managed_meeting(meeting_id, user)
    return {"linked": await asyncio.to_thread(db.auto_link_speakers, meeting_id)}


# ── ตรวจทาน transcript ──

class SegmentPatchIn(BaseModel):
    text: str | None = None
    speaker_name: str | None = None
    deleted: bool | None = None


@app.get("/meetings/{meeting_id}/segments")
async def list_segments(meeting_id: int, request: Request) -> list[dict]:
    user = _require_user(request)
    await _managed_meeting(meeting_id, user)
    return await asyncio.to_thread(db.list_segments, meeting_id)


@app.patch("/segments/{segment_id}")
async def patch_segment(segment_id: int, body: SegmentPatchIn, request: Request) -> dict:
    user = _require_user(request)
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    seg = await asyncio.to_thread(db.get_segment, segment_id)
    if not seg:
        raise HTTPException(status_code=404, detail="ไม่พบช่วงคำพูดนี้")
    meeting = await _managed_meeting(seg["meeting_id"], user)
    _require_status(meeting, "transcript_review", hint="แก้ transcript ได้เฉพาะขั้นตรวจทาน (หลังบอทหยุดและก่อนกดยืนยัน)")
    try:
        await asyncio.to_thread(
            db.edit_segment, segment_id, body.text, body.speaker_name, body.deleted
        )
    except ValueError as e:
        raise _value_error(e)
    return {"ok": True}


@app.post("/meetings/{meeting_id}/verify-transcript")
async def verify_transcript(meeting_id: int, request: Request) -> dict:
    """ยืนยันว่าตรวจ transcript แล้ว — ปลดล็อกการให้ AI สร้างรายงาน (ก่อนหน้านี้สร้างไม่ได้)"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    if _engine and _engine.is_running() and _current_meeting_id == meeting_id:
        raise HTTPException(status_code=409, detail="บอทยังบันทึกการประชุมนี้อยู่ — หยุดบอทก่อน")
    segments = await asyncio.to_thread(db.get_transcript, meeting_id)
    if not segments:
        raise HTTPException(status_code=409, detail="ไม่มีข้อความใน transcript ให้ยืนยัน")
    if not await asyncio.to_thread(db.set_meeting_status, meeting_id, "transcript_verified"):
        raise HTTPException(
            status_code=409, detail=f"ยืนยัน transcript ไม่ได้ในสถานะ \"{meeting['status']}\""
        )
    speakers = await asyncio.to_thread(db.list_speakers, meeting_id)
    return {
        "status": "transcript_verified",
        "unlinked_speakers": [s["display_name"] for s in speakers if s["participant_id"] is None and s["segment_count"]],
    }


@app.post("/meetings/{meeting_id}/reopen-transcript")
async def reopen_transcript(meeting_id: int, request: Request) -> dict:
    """กลับไปแก้ transcript อีกครั้ง (รายงานฉบับร่างที่มีอยู่จะล้าสมัย ต้องให้ AI สร้างใหม่) — รายงานที่อนุมัติแล้วทำไม่ได้"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    if not await asyncio.to_thread(db.set_meeting_status, meeting_id, "transcript_review"):
        raise HTTPException(status_code=409, detail=f"กลับไปแก้ transcript ไม่ได้ในสถานะ \"{meeting['status']}\"")
    return {"status": "transcript_review"}


@app.get("/meetings/{meeting_id}/transcript.txt")
async def download_transcript(meeting_id: int, request: Request) -> PlainTextResponse:
    user = _require_user(request)
    await _managed_meeting(meeting_id, user)
    rows = await asyncio.to_thread(db.get_transcript, meeting_id)
    lines = [f"[{r['spoken_at']:%H:%M:%S}] {r['display_name']}: {r['text']}" for r in rows]
    body = "\n".join(lines) + ("\n" if lines else "")
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="meet-transcript-{meeting_id}.txt"'},
    )


# ── รายงานการประชุม: สร้าง / แก้ / อนุมัติ / PDF ──

class MinutesPutIn(BaseModel):
    content: dict


class ApproveIn(BaseModel):
    confirm_warnings: bool = False


async def _validated(meeting_id: int, meeting: dict, content: dict) -> dict:
    """ตรวจเนื้อหารายงานใหม่กับรายชื่อ/transcript ปัจจุบัน (คำนวณคำเตือนสดทุกครั้งที่ดู/แก้/อนุมัติ)"""
    participants = await asyncio.to_thread(db.list_participants, meeting_id)
    rows = await asyncio.to_thread(db.get_transcript, meeting_id)
    when = meeting.get("started_at")
    return summarizer.validate_minutes(content, participants, [r["text"] for r in rows], when.date() if when else None)


@app.get("/meetings/{meeting_id}/minutes")
async def get_minutes(meeting_id: int, request: Request) -> dict:
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    minutes = await asyncio.to_thread(db.get_latest_minutes, meeting_id)
    participants = await asyncio.to_thread(db.list_participants, meeting_id)
    header = report_data.build_header(meeting, participants)
    out = {
        "status": meeting["status"],
        "minutes": None,
        "header": header,
        "header_warnings": report_data.header_warnings(header),
        "action_items": None,
        "editable": meeting["status"] == "draft",
        "legacy_summary": None,
    }
    if minutes:
        if minutes["status"] == "draft":     # ร่างที่ยังแก้อยู่: คำเตือนคำนวณสดให้ตรงกับรายชื่อ/transcript ปัจจุบัน
            minutes["content"] = await _validated(meeting_id, meeting, minutes["content"])
        else:
            out["action_items"] = await asyncio.to_thread(db.list_minutes_action_items, minutes["minutes_id"])
            minutes["approved_by_name"] = await asyncio.to_thread(db.get_user_name, minutes.get("approved_by"))
        out["minutes"] = minutes
    else:
        out["legacy_summary"] = await asyncio.to_thread(db.get_summary, meeting_id)
    return out


@app.post("/meetings/{meeting_id}/minutes/generate")
async def generate_minutes(meeting_id: int, request: Request) -> dict:
    """ให้ AI ร่างรายงาน (เวอร์ชันใหม่ ไม่ทับของเดิม) — ต้องยืนยัน transcript แล้ว"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "transcript_verified", "draft",
                    hint="ตรวจทานและกด \"ยืนยัน transcript\" ก่อนให้ AI สร้างรายงาน")
    try:
        saved = await asyncio.to_thread(summarizer.generate_for_meeting, meeting_id)
    except (RuntimeError, ValueError) as e:
        raise _value_error(e)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"เรียก Gemini ไม่สำเร็จ: {e!r}")
    return {"minutes_id": saved["minutes_id"], "version": saved["version"], "warnings": saved["content"]["warnings"]}


@app.put("/meetings/{meeting_id}/minutes")
async def put_minutes(meeting_id: int, body: MinutesPutIn, request: Request) -> dict:
    """บันทึกที่คนแก้ในรายงานฉบับร่าง (ตรวจและคำนวณคำเตือนใหม่ให้) — อนุมัติแล้วแก้ไม่ได้"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "draft", hint="รายงานที่อนุมัติแล้วถูกล็อก ใช้ \"สร้างเวอร์ชันแก้ไข\" เพื่อแก้ต่อ")
    minutes = await asyncio.to_thread(db.get_latest_minutes, meeting_id)
    if not minutes or minutes["status"] != "draft":
        raise HTTPException(status_code=409, detail="ไม่มีรายงานฉบับร่างให้แก้")
    allowed = {k: body.content.get(k) for k in ("summary", "agenda", "other_matters", "action_items")}
    try:
        content = await _validated(meeting_id, meeting, allowed)
    except (AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="รูปแบบเนื้อหารายงานไม่ถูกต้อง")
    if not await asyncio.to_thread(db.update_minutes_content, minutes["minutes_id"], content):
        raise HTTPException(status_code=409, detail="บันทึกไม่ได้ (รายงานถูกอนุมัติไปแล้ว)")
    return {"content": content}


@app.post("/meetings/{meeting_id}/minutes/approve")
async def approve_minutes(meeting_id: int, body: ApproveIn, request: Request) -> dict:
    """ประธาน/เลขา/เจ้าของอนุมัติรายงาน — ถ้ายังมีคำเตือนต้องยืนยัน (confirm_warnings) ว่าตรวจแล้ว"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "draft", hint="ต้องมีรายงานฉบับร่างก่อนอนุมัติ")
    minutes = await asyncio.to_thread(db.get_latest_minutes, meeting_id)
    if not minutes or minutes["status"] != "draft":
        raise HTTPException(status_code=409, detail="ไม่มีรายงานฉบับร่างให้อนุมัติ")
    content = await _validated(meeting_id, meeting, minutes["content"])
    participants = await asyncio.to_thread(db.list_participants, meeting_id)
    warnings = content["warnings"] + report_data.header_warnings(report_data.build_header(meeting, participants))
    if warnings and not body.confirm_warnings:
        raise HTTPException(
            status_code=409,
            detail={"message": "ยังมีรายการที่ควรตรวจก่อนอนุมัติ — ตรวจแล้วกดอนุมัติอีกครั้งเพื่อยืนยัน",
                    "warnings": warnings},
        )
    await asyncio.to_thread(db.update_minutes_content, minutes["minutes_id"], content)
    if not await asyncio.to_thread(db.approve_minutes, meeting_id, user["user_id"], content["action_items"]):
        raise HTTPException(status_code=409, detail="อนุมัติไม่ได้ (สถานะเปลี่ยนไปแล้ว)")
    return {"status": "approved"}


@app.post("/meetings/{meeting_id}/minutes/revise")
async def revise_minutes(meeting_id: int, request: Request) -> dict:
    """รายงานที่อนุมัติแล้วต้องแก้: สร้างเวอร์ชันใหม่ (draft) จากฉบับล่าสุด เวอร์ชันที่อนุมัติยังอยู่ในประวัติ"""
    user = _require_user(request)
    await _managed_meeting(meeting_id, user)
    saved = await asyncio.to_thread(db.revise_minutes, meeting_id)
    if not saved:
        raise HTTPException(status_code=409, detail="สร้างเวอร์ชันแก้ไขได้เฉพาะรายงานที่อนุมัติแล้ว")
    return saved


@app.get("/meetings/{meeting_id}/minutes.pdf")
async def download_minutes_pdf(meeting_id: int, request: Request) -> Response:
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    minutes = await asyncio.to_thread(db.get_latest_minutes, meeting_id)
    if not minutes:
        raise HTTPException(status_code=404, detail="การประชุมนี้ยังไม่มีรายงาน")
    participants = await asyncio.to_thread(db.list_participants, meeting_id)
    approved = minutes["status"] == "approved"
    pdf_bytes = await asyncio.to_thread(
        lambda: pdf_report.build_minutes_pdf(
            meeting, participants, minutes["content"],
            approved=approved,
            approved_by=db.get_user_name(minutes.get("approved_by")) if approved else None,
            approved_at=minutes.get("approved_at"),
        )
    )
    suffix = "" if approved else "-draft"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="meeting-minutes-{meeting_id}{suffix}.pdf"'},
    )


@app.get("/meetings/{meeting_id}/summary.pdf")
async def download_legacy_summary_pdf(meeting_id: int, request: Request) -> Response:
    """PDF สรุปแบบเก่า สำหรับการประชุมที่สรุปไว้ก่อนมีระบบรายงานการประชุม"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    summary = await asyncio.to_thread(db.get_summary, meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="การประชุมนี้ยังไม่ได้สรุป")
    pdf_bytes = await asyncio.to_thread(pdf_report.build_summary_pdf, meeting, summary)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="meeting-summary-{meeting_id}.pdf"'},
    )


# ── Google Calendar ──

@app.get("/auth/google")
async def auth_google(request: Request):
    _require_user(request)
    state = secrets.token_urlsafe(32)
    request.session["calendar_state"] = state
    # กลับมาหน้าเดิมหลังเชื่อมต่อเสร็จ (รับเฉพาะ path ภายในเว็บเรา กัน open-redirect ไปเว็บอื่น)
    back = request.query_params.get("return", "")
    request.session["calendar_return"] = back if back.startswith("/") and not back.startswith("//") else "/"
    return RedirectResponse(calendar_auth.build_auth_url(state))


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str | None = None, state: str | None = None):
    user = _require_user(request)
    expected = request.session.pop("calendar_state", None)
    if not code or not expected or not state or not secrets.compare_digest(state, expected):
        raise HTTPException(status_code=400, detail="การเชื่อม Calendar ไม่ถูกต้องหรือหมดอายุ — ลองใหม่อีกครั้ง")
    await asyncio.to_thread(calendar_auth.exchange_code, code, user["user_id"])
    return RedirectResponse(request.session.pop("calendar_return", None) or "/")


@app.get("/calendar/status")
async def calendar_status(request: Request) -> dict:
    user = _require_user(request)
    return {"connected": await asyncio.to_thread(calendar_auth.is_connected, user["user_id"])}


async def _sync_one(action_item_id: int, user: dict) -> str:
    """ส่ง action item เข้า Calendar ของผู้ใช้ — ผู้จัดการประชุมเท่านั้น และรายงานต้องอนุมัติแล้ว"""
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    meeting_id = await asyncio.to_thread(db.get_action_item_meeting, action_item_id)
    if meeting_id is None:
        raise HTTPException(status_code=404, detail="ไม่พบ action item นี้")
    meeting = await _managed_meeting(meeting_id, user)
    item = await asyncio.to_thread(db.get_action_item, action_item_id)
    if item and item.get("minutes_id") and meeting["status"] != "approved":
        raise HTTPException(status_code=409, detail="ส่งเข้า Calendar ได้หลังอนุมัติรายงานแล้วเท่านั้น")
    try:
        return await asyncio.to_thread(calendar_sync.sync_action_item, action_item_id, user["user_id"])
    except (RuntimeError, ValueError) as e:
        raise _value_error(e)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"เขียนลง Calendar ไม่สำเร็จ: {e!r}")


@app.post("/action-items/{action_item_id}/sync-to-calendar")
async def sync_action_item(action_item_id: int, request: Request) -> dict:
    user = _require_user(request)
    return {"google_calendar_event_id": await _sync_one(action_item_id, user)}


@app.post("/meetings/{meeting_id}/calendar/sync-all")
async def sync_all(meeting_id: int, request: Request) -> dict:
    """ส่ง action item ทั้งหมดของรายงานที่อนุมัติแล้วเข้า Calendar — รายการที่ส่งไม่ได้ (เช่น ไม่มีวัน) ไม่ล้มทั้งชุด"""
    user = _require_user(request)
    meeting = await _managed_meeting(meeting_id, user)
    _require_status(meeting, "approved", hint="ส่งเข้า Calendar ได้หลังอนุมัติรายงานแล้วเท่านั้น")
    minutes = await asyncio.to_thread(db.get_latest_minutes, meeting_id)
    items = await asyncio.to_thread(db.list_minutes_action_items, minutes["minutes_id"]) if minutes else []
    results = []
    for it in items:
        if it["calendar_synced"]:
            results.append({"action_item_id": it["action_item_id"], "ok": True, "already": True})
            continue
        try:
            await _sync_one(it["action_item_id"], user)
            results.append({"action_item_id": it["action_item_id"], "ok": True})
        except HTTPException as e:
            results.append({"action_item_id": it["action_item_id"], "ok": False, "error": e.detail})
    return {"results": results}


# ── WebSocket: ภาพสดจากบอท + สั่งเริ่ม/หยุด ──

@app.websocket("/ws")
async def ws(sock: WebSocket):
    user = _session_user(sock)
    if not user:
        await sock.close(code=1008)  # policy violation — ยังไม่ได้ล็อกอิน
        return
    user_id = user.get("user_id")
    email = user.get("email")
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _queues[q] = user_id

    async def pump():
        while True:
            ev = await q.get()
            await sock.send_json(ev)

    pump_task = asyncio.create_task(pump())
    try:
        is_run_owner = _run_owner_id is None or _run_owner_id == user_id
        if is_run_owner:
            for ev in _history[-600:]:
                await sock.send_json(ev)
        await sock.send_json(
            {"type": "state", "running": bool(is_run_owner and _engine and _engine.is_running())}
        )

        while True:
            msg = await sock.receive_json()
            action = msg.get("action")
            if action == "start":
                mid = msg.get("meeting_id")
                await _start((msg.get("url") or "").strip(), user_id, email, int(mid) if mid else None)
            elif action == "stop":
                await _stop(user_id)
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        _queues.pop(q, None)
