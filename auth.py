"""
ระบบ login ด้วย "Sign in with Google" — แยกจาก calendar_auth.py โดยตั้งใจ

ที่นี่ขอแค่สิทธิ์ identity (openid/email/profile) เพื่อรู้ว่าใครล็อกอิน ไม่ขอสิทธิ์ Calendar
(สิทธิ์ Calendar เป็นคนละ flow อยู่ใน calendar_auth.py — คนละจุดประสงค์ ไม่ควรปนกัน)

ใช้ GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET ตัวเดียวกับ calendar_auth.py ได้ (Client เดียวกัน
รองรับได้หลาย redirect URI) — แค่ต้องเพิ่ม redirect URI นี้ใน Google Cloud Console ด้วย:
http://localhost:8000/auth/login/callback
"""

import os

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from google_auth_oauthlib.flow import Flow

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
REDIRECT_URI = "http://localhost:8000/auth/login/callback"


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
    # ปิด PKCE เหมือน calendar_auth.py — build_login_url()/exchange_login_code() คนละ request กัน
    return Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI, autogenerate_code_verifier=False
    )


def build_login_url() -> str:
    auth_url, _ = _flow().authorization_url(access_type="online", prompt="select_account")
    return auth_url


def exchange_login_code(code: str) -> dict:
    """แลก code เป็นข้อมูลผู้ใช้จาก Google ID token: google_sub, email, name, picture"""
    flow = _flow()
    flow.fetch_token(code=code)
    claims = google_id_token.verify_oauth2_token(
        flow.credentials.id_token, google_requests.Request(), audience=os.environ["GOOGLE_CLIENT_ID"]
    )
    return {
        "google_sub": claims["sub"],
        "email": claims.get("email"),
        "name": claims.get("name") or claims.get("email"),
        "picture": claims.get("picture"),
    }
