"""
OAuth ไปยัง Google Calendar — สำหรับเขียน action item กลับเป็น event ในปฏิทินของผู้ใช้

ต้องตั้งค่า GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET ใน .env ก่อน (สร้างที่
https://console.cloud.google.com/ — Enable Calendar API + OAuth consent screen +
OAuth Client ID ประเภท Web application, redirect URI
http://localhost:8000/auth/google/callback)

Token ที่ได้เก็บไว้ที่ calendar_token.json (gitignore แล้ว)
"""

import os
import pathlib

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
REDIRECT_URI = "http://localhost:8000/auth/google/callback"
TOKEN_PATH = pathlib.Path(__file__).parent / "calendar_token.json"


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


def build_auth_url() -> str:
    auth_url, _ = _flow().authorization_url(access_type="offline", prompt="consent")
    return auth_url


def exchange_code(code: str) -> None:
    flow = _flow()
    flow.fetch_token(code=code)
    TOKEN_PATH.write_text(flow.credentials.to_json(), encoding="utf-8")


def is_connected() -> bool:
    return TOKEN_PATH.exists()


def get_credentials() -> Credentials | None:
    """คืน credentials ที่ใช้งานได้ (รีเฟรชให้ถ้าหมดอายุ) หรือ None ถ้ายังไม่เชื่อมต่อ"""
    if not TOKEN_PATH.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds
