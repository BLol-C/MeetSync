"""
บริการบอทของ MeetSync (FastAPI) — ทำหน้าที่เดียว: คุมบอทที่เข้าห้อง Google Meet และบันทึกคำบรรยายลงฐานข้อมูล
หน้าเว็บทั้งหมดอยู่ที่ Streamlit (streamlit_app.py) ซึ่งเรียกบริการนี้ผ่าน botclient.py

แยกเป็นคนละโปรเซสโดยตั้งใจ: บอทต้องอยู่ในห้องประชุมได้นาน 1 ชั่วโมงขึ้นไป ต่อให้หน้าเว็บรีโหลด/ค้าง/ถูกปิดไปแล้วบอทก็ไม่หยุด

รัน:  venv\\Scripts\\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000   (หรือใช้ run.ps1 เปิดทั้งสองส่วนพร้อมกัน)

ความปลอดภัย: ทุกคำขอ /bot/* ต้องมีโทเคนลับ (botclient.get_token) และรับเฉพาะเครื่องเดียวกัน ผู้ใช้ที่ส่งมาในเฮดเดอร์
ถูกตรวจสิทธิ์รายการประชุมทุกครั้ง (เจ้าของ หรือประธาน/เลขาตามอีเมล)
"""

import asyncio
import contextlib
import secrets
import time
import urllib.parse
from collections import OrderedDict, deque

from dotenv import load_dotenv

load_dotenv()  # ต้องโหลดก่อน import db เพราะ db.py อ่าน env ตอน import (module level)

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired
from pydantic import BaseModel

import botclient
import calendar_auth
import db
from meet_engine import MeetCaptionEngine

_ROW_MAP_MAX = 500     # จำนวนแถวคำบรรยายล่าสุดที่จำ segment_id ไว้ (แถวเก่ากว่านี้ไม่กลับมา final ซ้ำแล้ว)
_LIVE_ROWS_MAX = 150   # จำนวนแถวคำบรรยายสดที่เก็บให้หน้าเว็บอ่าน
_STATUS_MAX = 30

_engine: MeetCaptionEngine | None = None
_db_ready = False
_current_meeting_id: int | None = None
_last_meeting_id: int | None = None   # การประชุมของรอบล่าสุด (คงไว้หลังหยุด เพื่อให้ผู้จัดการยังเห็นสถานะ/ข้อผิดพลาดของรอบนั้น)
_run_owner_id: int | None = None   # user_id ของคนที่สั่งเริ่มบอทรอบนี้
_last_error: str | None = None
_seq_no = 0
_segment_by_row: OrderedDict[int, int] = OrderedDict()   # id แถวคำบรรยาย (จาก JS) -> segment_id ใน DB
                                                          # กันไม่ให้คนพูดยาวคนเดียวถูกบันทึกเป็นหลายแถวซ้ำ ๆ
_live_rows: OrderedDict[int, dict] = OrderedDict()       # คำบรรยายล่าสุด (รวมที่ยังไม่ final) ให้หน้าเว็บแสดงสด
_status_log: deque = deque(maxlen=_STATUS_MAX)
_save_queue: asyncio.Queue | None = None    # คิวเขียน segment ลง DB (ประมวลผลทีละอัน ตามลำดับ)
_save_worker: asyncio.Task | None = None
_finalize_lock = asyncio.Lock()


@contextlib.asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _db_ready
    try:
        await asyncio.to_thread(db.init_schema)
        _db_ready = True
    except Exception as e:  # noqa: BLE001 — DB ต่อไม่ได้ก็ให้บริการเปิดอยู่ (หน้าเว็บจะเห็นจาก /health)
        _db_ready = False
        _log(f"⚠️ เชื่อมต่อฐานข้อมูลไม่สำเร็จ: {e!r}")
    yield
    await _finalize()


app = FastAPI(lifespan=_lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _log(text: str):
    _status_log.append({"t": time.strftime("%H:%M:%S"), "text": text})


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
        except Exception as e:  # noqa: BLE001 — เขียน DB พลาดต้องไม่ทำให้ worker/บริการล่ม
            _log(f"⚠️ บันทึกลง DB ไม่สำเร็จ: {e!r}")
        finally:
            q.task_done()


def _handle_event(ev: dict):
    """รับ event จากเอนจินบอท: เก็บไว้ให้หน้าเว็บอ่านสด และส่งคำบรรยายที่ final เข้าคิวเขียน DB"""
    global _last_error
    kind = ev.get("type")
    if kind == "status":
        _log(ev.get("text", ""))
    elif kind == "error":
        _last_error = ev.get("text", "")
        _log("ผิดพลาด: " + _last_error)
    elif kind == "caption":
        name, text = ev.get("name"), (ev.get("text") or "").strip()
        if not name or name == "(raw)" or not text:
            return
        row_id = ev.get("id")
        _live_rows[row_id] = {"id": row_id, "name": name, "text": text, "final": bool(ev.get("final"))}
        _live_rows.move_to_end(row_id)
        while len(_live_rows) > _LIVE_ROWS_MAX:
            _live_rows.popitem(last=False)
        if ev.get("final") and _db_ready and _current_meeting_id is not None and _save_queue is not None:
            _save_queue.put_nowait((_current_meeting_id, row_id, name, text))


def _on_engine_event(engine: MeetCaptionEngine, ev: dict):
    if engine is not _engine:
        return  # event ตกค้างจากบอทตัวเก่าที่ถูกแทนไปแล้ว
    if ev.get("type") == "ended":
        # บอทจบเอง (ประชุมจบ/เบราว์เซอร์ปิด/error) -> ปิดประชุมใน DB ให้เรียบร้อย
        asyncio.get_running_loop().create_task(_finalize(only_if_engine=engine))
        return
    _handle_event(ev)


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
                _log(f"⚠️ ปิดบันทึกการประชุมใน DB ไม่สำเร็จ: {e!r}")
        _current_meeting_id = None
        _log("บอทหยุดแล้ว — ไปตรวจทาน transcript ได้")


# ── ยืนยันตัวตน ──

def identity(
    x_bot_token: str = Header(default=""),
    x_user_id: str = Header(default=""),
    x_user_email: str = Header(default=""),
) -> dict:
    if not secrets.compare_digest(x_bot_token or "", botclient.get_token()):
        raise HTTPException(status_code=401, detail="โทเคนบริการบอทไม่ถูกต้อง")
    try:
        user_id = int(x_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="ไม่ทราบผู้ใช้")
    return {"user_id": user_id, "email": urllib.parse.unquote(x_user_email) or None}


def _is_running() -> bool:
    return bool(_engine and _engine.is_running())


async def _manager_of(meeting_id: int, user: dict) -> dict | None:
    meeting = await asyncio.to_thread(db.get_meeting, meeting_id)
    if meeting and await asyncio.to_thread(db.is_meeting_manager, meeting, user["user_id"], user["email"]):
        return meeting
    return None


# ── เส้นทาง ──

class StartIn(BaseModel):
    meeting_id: int


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "db": _db_ready, "running": _is_running()}


@app.post("/bot/start")
async def bot_start(body: StartIn, user: dict = Depends(identity)) -> dict:
    """เริ่มบอท (หรือกลับมาบันทึกต่อหลังบอทหลุด) สำหรับการประชุมที่ตั้งค่าไว้แล้ว"""
    global _engine, _current_meeting_id, _last_meeting_id, _seq_no, _run_owner_id, _save_queue, _save_worker, _last_error
    if not _db_ready:
        raise HTTPException(status_code=503, detail="ฐานข้อมูลไม่พร้อม เริ่มบอทไม่ได้")
    meeting = await _manager_of(body.meeting_id, user)
    if meeting is None:
        raise HTTPException(status_code=404, detail="ไม่พบการประชุมนี้")
    if _is_running() and _current_meeting_id != body.meeting_id:
        raise HTTPException(status_code=409, detail="บอทกำลังบันทึกการประชุมอื่นอยู่ — หยุดอันนั้นก่อน")
    if _is_running():
        raise HTTPException(status_code=409, detail="บอทกำลังบันทึกการประชุมนี้อยู่แล้ว")

    await _finalize()   # เก็บกวาดรอบก่อนหน้า (ถ้ามีค้าง) ให้เรียบร้อย
    if not await asyncio.to_thread(db.begin_recording, body.meeting_id):
        raise HTTPException(status_code=409, detail="เริ่มบันทึกการประชุมนี้ไม่ได้ (ผ่านขั้นตรวจทาน transcript ไปแล้ว)")

    _seq_no = await asyncio.to_thread(db.max_sequence_no, body.meeting_id)   # บันทึกต่อ: ลำดับไม่ชนของเดิม
    _segment_by_row.clear()
    _live_rows.clear()
    _status_log.clear()
    _last_error = None
    _run_owner_id = user["user_id"]
    _current_meeting_id = _last_meeting_id = body.meeting_id
    _save_queue = asyncio.Queue()
    _save_worker = asyncio.create_task(_save_worker_loop(_save_queue))

    engine: MeetCaptionEngine = MeetCaptionEngine(on_event=lambda ev: _on_engine_event(engine, ev))
    _engine = engine
    engine.start(meeting["meet_url"])
    _log("กำลังเริ่มบอท…")
    return {"ok": True}


@app.post("/bot/stop")
async def bot_stop(user: dict = Depends(identity)) -> dict:
    if _current_meeting_id is not None and not await _manager_of(_current_meeting_id, user):
        raise HTTPException(status_code=403, detail="บอทนี้กำลังบันทึกการประชุมของคนอื่น — หยุดไม่ได้")
    await _finalize()
    return {"ok": True}


@app.get("/bot/state")
async def bot_state(user: dict = Depends(identity)) -> dict:
    """สถานะบอทและคำบรรยายสด — เห็นรายละเอียดเฉพาะผู้จัดการของการประชุมที่กำลังบันทึก (คนอื่นเห็นแค่ว่าบอทไม่ว่าง)"""
    running = _is_running()
    visible = _last_meeting_id is not None and bool(await _manager_of(_last_meeting_id, user))
    if not visible:
        return {"running": running, "mine": False, "meeting_id": None, "rows": [], "status": [], "error": None}
    return {
        "running": running,
        "mine": True,
        "meeting_id": _last_meeting_id,
        "rows": list(_live_rows.values()),
        "status": list(_status_log),
        "error": _last_error,
    }


# ── เชื่อม Google Calendar (OAuth) — ไม่ใช้ session ใช้โทเคนที่เซ็นแล้วระบุตัวผู้ใช้ ──

def _safe_return(url: str) -> str:
    """ปลายทางหลังเชื่อมเสร็จต้องเป็นหน้าเว็บของเราเท่านั้น (กัน open redirect)"""
    return url if url.startswith(botclient.UI_URL) else botclient.UI_URL


@app.get("/calendar/connect")
async def calendar_connect(t: str):
    try:
        botclient.read_calendar_token(t, max_age=600)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="ลิงก์เชื่อม Calendar ไม่ถูกต้องหรือหมดอายุ — กลับไปกดปุ่มเชื่อมต่อใหม่")
    return RedirectResponse(calendar_auth.build_auth_url(t))


@app.get("/auth/google/callback")
async def calendar_callback(code: str | None = None, state: str | None = None):
    try:
        data = botclient.read_calendar_token(state or "", max_age=900)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="การเชื่อม Calendar ไม่ถูกต้องหรือหมดอายุ — ลองใหม่อีกครั้ง")
    if not code:
        raise HTTPException(status_code=400, detail="Google ไม่ได้ส่งรหัสยืนยันกลับมา (ยกเลิกการอนุญาตหรือเปล่า?)")
    await asyncio.to_thread(calendar_auth.exchange_code, code, data["uid"])
    return RedirectResponse(_safe_return(data["ret"]))
