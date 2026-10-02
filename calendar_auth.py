"""
OAuth ไปยัง Google Calendar — สำหรับเขียน action item กลับเป็น event ในปฏิทินของผู้ใช้

ต้องตั้งค่า GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET ใน .env ก่อน (สร้างที่
https://console.cloud.google.com/ — Enable Calendar API + OAuth consent screen +
OAuth Client ID ประเภท Web application, redirect URI
http://localhost:8000/auth/google/callback)

Token เก็บในตาราง calendar_tokens แยกตามผู้ใช้ — event จะเข้าปฏิทินของคนที่กดส่งเอง ไม่ใช่ปฏิทินของคนที่
เชื่อมต่อทีหลังสุดเหมือนเวอร์ชันก่อน (ที่เก็บ token เป็นไฟล์ calendar_token.json เดียวทั้งเซิร์ฟเวอร์ — ตอนนี้ไม่ใช้แล้ว
ผู้ใช้ต้องกด "เชื่อมต่อ Google Calendar" ใหม่หนึ่งครั้ง)
"""

import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

import db

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
REDIRECT_URI = "http://localhost:8000/auth/google/callback"


def _client_config() -> dict:
    return {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def _flow() -> Flow:
    # ปิด PKCE (autogenerate_code_verifier) เพราะ build_auth_url()/exchange_code() สร้าง Flow
    # คนละอินสแตนซ์กันคนละ request — ไม่มีที่เก็บ code_verifier ข้าม request ให้ตรงกัน
    # ปิดได้เพราะเราเป็น confidential client (มี client_secret) อยู่แล้ว ไม่ต้องพึ่ง PKCE
    return Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI, autogenerate_code_verifier=False
    )


def build_auth_url(state: str) -> str:
    """state ต้องเป็นค่าสุ่มที่ผู้เรียกเก็บใน session แล้วตรวจซ้ำตอน callback (กัน CSRF)"""
    auth_url, _ = _flow().authorization_url(access_type="offline", prompt="consent", state=state)
    return auth_url


def exchange_code(code: str, user_id: int) -> None:
    flow = _flow()
    flow.fetch_token(code=code)
    db.save_calendar_token(user_id, flow.credentials.to_json())


def is_connected(user_id: int) -> bool:
    return db.get_calendar_token(user_id) is not None


def get_credentials(user_id: int) -> Credentials | None:
    """คืน credentials ที่ใช้งานได้ (รีเฟรชให้ถ้าหมดอายุ) หรือ None ถ้าผู้ใช้คนนี้ยังไม่เชื่อมต่อ"""
    token = db.get_calendar_token(user_id)
    if not token:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(token), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        db.save_calendar_token(user_id, creds.to_json())
    return creds
