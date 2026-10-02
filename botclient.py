"""
ไคลเอนต์ของ "บริการบอท" (app.py) สำหรับหน้าเว็บ + โทเคนลับที่สองฝั่งใช้ร่วมกัน

หน้าเว็บ (Streamlit) กับบริการบอท (FastAPI) เป็นคนละโปรเซส คุยกันผ่าน HTTP ในเครื่องเดียวกัน (127.0.0.1) โดยแนบโทเคนลับ
ทุกคำขอ ผู้ใช้ที่ล็อกอินแล้ว (ตรวจโดยหน้าเว็บ) ถูกส่งไปในเฮดเดอร์ — บริการบอทเชื่อตามนั้นเพราะรับเฉพาะคำขอที่มีโทเคนถูกต้อง

โทเคน: ตัวแปร BOT_API_TOKEN ใน .env หรือถ้าไม่ตั้ง จะสร้างไฟล์ .bot_token ให้อัตโนมัติ (สุ่ม 64 ตัวอักษร ไม่ commit เข้า git)
"""

import os
import pathlib
import secrets
import urllib.parse

import requests
from itsdangerous import URLSafeTimedSerializer

BOT_API_URL = os.environ.get("BOT_API_URL", "http://127.0.0.1:8000")      # หน้าเว็บเรียกบอทที่นี่
BOT_PUBLIC_URL = os.environ.get("BOT_PUBLIC_URL", "http://localhost:8000")   # เบราว์เซอร์ผู้ใช้ไปที่นี่ (ตอนเชื่อม Calendar)
UI_URL = os.environ.get("STREAMLIT_URL", "http://localhost:8501")
TOKEN_FILE = pathlib.Path(__file__).parent / ".bot_token"
TIMEOUT = 8


def get_token() -> str:
    env = os.environ.get("BOT_API_TOKEN", "").strip()
    if env:
        return env
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    new = secrets.token_hex(32)
    try:   # สร้างแบบ exclusive: ถ้าสองโปรเซสสตาร์ทพร้อมกัน คนที่แพ้จะอ่านค่าของคนที่ชนะ ไม่ใช่สร้างคนละค่า
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new)
        return new
    except FileExistsError:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_token(), salt="calendar-connect")


def calendar_connect_token(user_id: int, return_url: str, email: str | None = None) -> str:
    """โทเคนที่เซ็นแล้วบอกว่า "ผู้ใช้คนนี้ขอเชื่อม Calendar แล้วให้กลับมาที่หน้านี้" (อายุสั้น) — ใช้เป็น state ของ OAuth ด้วย
    email = อีเมลที่ล็อกอินอยู่ ใช้เป็น login_hint ให้ Google เลือกบัญชีเดียวกัน"""
    data = {"uid": user_id, "ret": return_url}
    if email:
        data["em"] = email
    return _signer().dumps(data)


def read_calendar_token(token: str, max_age: int) -> dict:
    return _signer().loads(token, max_age=max_age)


def calendar_connect_url(user: dict, return_url: str | None = None) -> str:
    return f"{BOT_PUBLIC_URL}/calendar/connect?t=" + urllib.parse.quote(
        calendar_connect_token(user["user_id"], return_url or UI_URL, user.get("email"))
    )


class BotError(Exception):
    """บอทสั่งไม่ได้ — message เป็นภาษาไทยพร้อมแสดงให้ผู้ใช้; unreachable = บริการบอทไม่ได้เปิดอยู่"""

    def __init__(self, message: str, unreachable: bool = False):
        super().__init__(message)
        self.unreachable = unreachable


def headers_for(user: dict) -> dict:
    return {
        "X-Bot-Token": get_token(),
        "X-User-Id": str(user["user_id"]),
        "X-User-Email": urllib.parse.quote(user.get("email") or ""),
    }


def _call(method: str, path: str, user: dict, **kwargs) -> dict:
    try:
        r = requests.request(method, BOT_API_URL + path, headers=headers_for(user), timeout=TIMEOUT, **kwargs)
    except requests.ConnectionError:
        raise BotError("ติดต่อบริการบอทไม่ได้ — ตรวจว่าเปิด run.ps1 (หรือ uvicorn app:app) อยู่", unreachable=True)
    except requests.Timeout:
        raise BotError("บริการบอทตอบช้าเกินไป ลองใหม่อีกครั้ง", unreachable=True)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        raise BotError(detail if isinstance(detail, str) else f"บริการบอทตอบข้อผิดพลาด ({r.status_code})")
    return r.json()


def start(user: dict, meeting_id: int) -> None:
    _call("POST", "/bot/start", user, json={"meeting_id": meeting_id})


def stop(user: dict) -> None:
    _call("POST", "/bot/stop", user)


def state(user: dict) -> dict:
    return _call("GET", "/bot/state", user)


def health() -> dict | None:
    """สถานะบริการบอท หรือ None ถ้าไม่ได้เปิดอยู่ (ไม่ต้องใช้ผู้ใช้/โทเคน)"""
    try:
        return requests.get(BOT_API_URL + "/health", timeout=3).json()
    except (requests.RequestException, ValueError):
        return None
