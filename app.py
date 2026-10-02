"""
เว็บแอปโลคอลสำหรับ MeetSync — วางลิงก์ Google Meet แล้วดู transcript แบบเรียลไทม์

รัน:  venv\\Scripts\\python.exe -m uvicorn app:app --port 8000
เปิด: http://localhost:8000
"""

import asyncio
import contextlib
import pathlib
import secrets
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()  # ต้องโหลดก่อน import db เพราะ db.py อ่าน env ตอน import (module level)

import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

import auth
import calendar_auth
import calendar_sync
import db
import pdf_report
import summarizer
from meet_engine import MEET_URL_RE, MeetCaptionEngine

HERE = pathlib.Path(__file__).parent
INDEX_HTML = (HERE / "frontend.html").read_text(encoding="utf-8")

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


async def _start(url: str, user_id: int | None):
    global _engine, _current_meeting_id, _seq_no, _run_owner_id, _save_queue, _save_worker
    if not MEET_URL_RE.match(url):
        _send_to_user(user_id, {"type": "error", "text": "ลิงก์ไม่ถูกต้อง (ต้องเป็น https://meet.google.com/xxx-xxxx-xxx)"})
        return
    if _bot_busy_with_other(user_id):
        _send_to_user(user_id, {"type": "error", "text": "บอทกำลังถูกใช้งานโดยผู้ใช้อื่น — รอให้การประชุมนั้นจบก่อน"})
        return
    await _finalize()  # ปิดการประชุมก่อนหน้า (ถ้ามี) ให้เรียบร้อยก่อนเริ่มรอบใหม่

    _run_owner_id = user_id
    _history.clear()
    _broadcast({"type": "cleared"})
    _broadcast({"type": "state", "running": True})

    _current_meeting_id = None
    _seq_no = 0
    _segment_by_row.clear()
    if _db_ready:
        try:
            _current_meeting_id = await asyncio.to_thread(db.create_meeting, url, user_id)
        except Exception as e:  # noqa: BLE001
            _broadcast({"type": "status", "text": f"⚠️ สร้างบันทึกการประชุมใน DB ไม่สำเร็จ: {e!r}"})
        else:
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


async def _owned_meeting(meeting_id: int, user: dict) -> dict:
    """ดึงการประชุมที่เป็นของ user นี้ — ไม่พบหรือไม่ใช่ของเขาตอบ 404 เหมือนกัน (ไม่เปิดเผยว่ามีอยู่จริง)"""
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    meeting = await asyncio.to_thread(db.get_meeting, meeting_id)
    if not meeting or meeting["owner_user_id"] != user.get("user_id"):
        raise HTTPException(status_code=404, detail="ไม่พบการประชุมนี้")
    return meeting


@app.get("/")
async def index(request: Request) -> HTMLResponse:
    if not _session_user(request):
        return RedirectResponse("/login")
    return HTMLResponse(INDEX_HTML)


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


@app.get("/meetings")
async def list_meetings(request: Request) -> list[dict]:
    user = _require_user(request)
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    return await asyncio.to_thread(db.list_meetings, user["user_id"])


@app.get("/meetings/{meeting_id}/transcript.txt")
async def download_transcript(meeting_id: int, request: Request) -> PlainTextResponse:
    user = _require_user(request)
    await _owned_meeting(meeting_id, user)
    rows = await asyncio.to_thread(db.get_transcript, meeting_id)
    lines = [f"[{r['spoken_at']:%H:%M:%S}] {r['display_name']}: {r['text']}" for r in rows]
    body = "\n".join(lines) + ("\n" if lines else "")
    return PlainTextResponse(
        body,
        headers={"Content-Disposition": f'attachment; filename="meet-transcript-{meeting_id}.txt"'},
    )


@app.get("/meetings/{meeting_id}/summary.pdf")
async def download_summary_pdf(meeting_id: int, request: Request) -> Response:
    user = _require_user(request)
    meeting = await _owned_meeting(meeting_id, user)
    summary = await asyncio.to_thread(db.get_summary, meeting_id)
    if not summary:
        raise HTTPException(status_code=404, detail="การประชุมนี้ยังไม่ได้สรุป")
    pdf_bytes = await asyncio.to_thread(pdf_report.build_summary_pdf, meeting, summary)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="meeting-summary-{meeting_id}.pdf"'},
    )


@app.post("/meetings/{meeting_id}/summarize")
async def summarize_meeting(meeting_id: int, request: Request, regenerate: bool = False) -> dict:
    user = _require_user(request)
    await _owned_meeting(meeting_id, user)
    try:
        if regenerate:
            await asyncio.to_thread(db.delete_summary, meeting_id)
        return await asyncio.to_thread(summarizer.summarize_meeting, meeting_id)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"เรียก Gemini ไม่สำเร็จ: {e!r}")


@app.get("/auth/google")
async def auth_google(request: Request):
    _require_user(request)
    state = secrets.token_urlsafe(32)
    request.session["calendar_state"] = state
    return RedirectResponse(calendar_auth.build_auth_url(state))


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str | None = None, state: str | None = None):
    _require_user(request)
    expected = request.session.pop("calendar_state", None)
    if not code or not expected or not state or not secrets.compare_digest(state, expected):
        raise HTTPException(status_code=400, detail="การเชื่อม Calendar ไม่ถูกต้องหรือหมดอายุ — ลองใหม่อีกครั้ง")
    await asyncio.to_thread(calendar_auth.exchange_code, code)
    return RedirectResponse("/")


@app.get("/calendar/status")
async def calendar_status(request: Request) -> dict:
    _require_user(request)
    return {"connected": calendar_auth.is_connected()}


@app.post("/action-items/{action_item_id}/sync-to-calendar")
async def sync_action_item(action_item_id: int, request: Request) -> dict:
    user = _require_user(request)
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    owner_id = await asyncio.to_thread(db.get_action_item_owner, action_item_id)
    if owner_id is None or owner_id != user.get("user_id"):
        raise HTTPException(status_code=404, detail="ไม่พบ action item นี้")
    try:
        event_id = await asyncio.to_thread(calendar_sync.sync_action_item, action_item_id)
        return {"google_calendar_event_id": event_id}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"เขียนลง Calendar ไม่สำเร็จ: {e!r}")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    user = _session_user(sock)
    if not user:
        await sock.close(code=1008)  # policy violation — ยังไม่ได้ล็อกอิน
        return
    user_id = user.get("user_id")
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
                await _start((msg.get("url") or "").strip(), user_id)
            elif action == "stop":
                await _stop(user_id)
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        _queues.pop(q, None)
