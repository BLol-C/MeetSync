"""
เว็บแอปโลคอลสำหรับ MeetSync — วางลิงก์ Google Meet แล้วดู transcript แบบเรียลไทม์

รัน:  venv\\Scripts\\python.exe -m uvicorn app:app --port 8000
เปิด: http://localhost:8000
"""

import asyncio
import contextlib
import pathlib

from dotenv import load_dotenv

load_dotenv()  # ต้องโหลดก่อน import db เพราะ db.py อ่าน env ตอน import (module level)

import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

import auth
import calendar_auth
import calendar_sync
import db
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
    if _engine:
        await _engine.stop()


app = FastAPI(lifespan=_lifespan)
app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET"])

_queues: set[asyncio.Queue] = set()
_history: list[dict] = []          # status + caption(final) ล่าสุด สำหรับ client ที่เพิ่งต่อ
_engine: MeetCaptionEngine | None = None
_db_ready = False
_current_meeting_id: int | None = None
_seq_no = 0
_segment_by_row: dict[int, int] = {}   # id ของแถวคำบรรยาย (จาก JS) -> segment_id ใน DB
                                        # กันไม่ให้คนพูดยาวคนเดียวถูกบันทึกเป็นหลายแถวซ้ำ ๆ


def _save_segment(ev: dict):
    """บันทึกช่วงคำพูดที่ final แล้วลง MySQL — ล้มเหลวได้โดยไม่ทำ engine/เว็บล่ม

    แถวคำบรรยายเดียวกัน (ev["id"]) อาจ final ซ้ำหลายครั้งระหว่างที่คนคนเดิมพูดต่อ
    (เช่น หยุดหายใจ/เว้นจังหวะเกิน 1.5 วิ) — ครั้งแรก insert แถวใหม่ ครั้งต่อ ๆ ไป update
    แถวเดิมด้วยข้อความที่ยาวขึ้น แทนที่จะ insert ซ้ำ
    """
    global _seq_no
    if not _db_ready or _current_meeting_id is None:
        return
    name = ev.get("name")
    text = (ev.get("text") or "").strip()
    row_id = ev.get("id")
    if not name or name == "(raw)" or not text:
        return
    try:
        segment_id = _segment_by_row.get(row_id)
        if segment_id is not None:
            db.update_segment(segment_id, text)
            return
        speaker_id = db.get_or_create_speaker(_current_meeting_id, name)
        _seq_no += 1
        segment_id = db.insert_segment(_current_meeting_id, speaker_id, _seq_no, text)
        if row_id is not None:
            _segment_by_row[row_id] = segment_id
    except Exception as e:  # noqa: BLE001
        _broadcast({"type": "status", "text": f"⚠️ บันทึกลง DB ไม่สำเร็จ: {e!r}"})


def _broadcast(ev: dict):
    if ev.get("type") == "status" or (ev.get("type") == "caption" and ev.get("final")):
        _history.append(ev)
        if len(_history) > 1500:
            del _history[:750]
    if ev.get("type") == "caption" and ev.get("final"):
        _save_segment(ev)
    if ev.get("type") == "ended":
        ev = {"type": "state", "running": False}
        _history.append(ev)
    for q in list(_queues):
        q.put_nowait(ev)


async def _start(url: str):
    global _engine, _current_meeting_id, _seq_no
    if not MEET_URL_RE.match(url):
        _broadcast({"type": "error", "text": "ลิงก์ไม่ถูกต้อง (ต้องเป็น https://meet.google.com/xxx-xxxx-xxx)"})
        return
    if _engine and _engine.is_running():
        await _engine.stop()
    _history.clear()
    _broadcast({"type": "cleared"})
    _broadcast({"type": "state", "running": True})

    _current_meeting_id = None
    _seq_no = 0
    _segment_by_row.clear()
    if _db_ready:
        try:
            _current_meeting_id = db.create_meeting(url)
        except Exception as e:  # noqa: BLE001
            _broadcast({"type": "status", "text": f"⚠️ สร้างบันทึกการประชุมใน DB ไม่สำเร็จ: {e!r}"})

    _engine = MeetCaptionEngine(on_event=_broadcast)
    _engine.start(url)


async def _stop():
    global _engine, _current_meeting_id
    if _engine:
        await _engine.stop()
        _engine = None
    ended_meeting_id = _current_meeting_id
    if _db_ready and _current_meeting_id is not None:
        try:
            db.end_meeting(_current_meeting_id)
        except Exception as e:  # noqa: BLE001
            _broadcast({"type": "status", "text": f"⚠️ ปิดบันทึกการประชุมใน DB ไม่สำเร็จ: {e!r}"})
        _current_meeting_id = None
    _broadcast({"type": "state", "running": False})
    if ended_meeting_id is not None:
        _broadcast({"type": "meeting_ended", "meeting_id": ended_meeting_id})


@app.get("/")
async def index(request: Request) -> HTMLResponse:
    if not request.session.get("user"):
        return RedirectResponse("/login")
    return HTMLResponse(INDEX_HTML)


@app.get("/login")
async def login_page() -> HTMLResponse:
    return HTMLResponse(LOGIN_HTML)


@app.get("/auth/login")
async def auth_login():
    return RedirectResponse(auth.build_login_url())


@app.get("/auth/login/callback")
async def auth_login_callback(request: Request, code: str):
    info = await asyncio.to_thread(auth.exchange_login_code, code)
    if _db_ready:
        await asyncio.to_thread(
            db.upsert_user, info["google_sub"], info["email"], info["name"], info["picture"]
        )
    request.session["user"] = {
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
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="ยังไม่ได้เข้าสู่ระบบ")
    return user


@app.get("/meetings")
async def list_meetings() -> list[dict]:
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม")
    return await asyncio.to_thread(db.list_meetings)


@app.post("/meetings/{meeting_id}/summarize")
async def summarize_meeting(meeting_id: int, regenerate: bool = False) -> dict:
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม สรุปไม่ได้")
    try:
        if regenerate:
            await asyncio.to_thread(db.delete_summary, meeting_id)
        return await asyncio.to_thread(summarizer.summarize_meeting, meeting_id)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"เรียก Gemini ไม่สำเร็จ: {e!r}")


@app.get("/auth/google")
async def auth_google():
    return RedirectResponse(calendar_auth.build_auth_url())


@app.get("/auth/google/callback")
async def auth_google_callback(code: str):
    await asyncio.to_thread(calendar_auth.exchange_code, code)
    return RedirectResponse("/")


@app.get("/calendar/status")
async def calendar_status() -> dict:
    return {"connected": calendar_auth.is_connected()}


@app.post("/action-items/{action_item_id}/sync-to-calendar")
async def sync_action_item(action_item_id: int) -> dict:
    try:
        event_id = await asyncio.to_thread(calendar_sync.sync_action_item, action_item_id)
        return {"google_calendar_event_id": event_id}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"เขียนลง Calendar ไม่สำเร็จ: {e!r}")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue()
    _queues.add(q)

    async def pump():
        while True:
            ev = await q.get()
            await sock.send_json(ev)

    pump_task = asyncio.create_task(pump())
    try:
        for ev in _history[-600:]:
            await sock.send_json(ev)
        await sock.send_json({"type": "state", "running": bool(_engine and _engine.is_running())})

        while True:
            msg = await sock.receive_json()
            action = msg.get("action")
            if action == "start":
                await _start((msg.get("url") or "").strip())
            elif action == "stop":
                await _stop()
            elif action == "clear":
                _history.clear()
                _broadcast({"type": "cleared"})
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        _queues.discard(q)
