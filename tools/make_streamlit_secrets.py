"""
สร้าง .streamlit/secrets.toml สำหรับล็อกอิน Google ของ Streamlit (st.login) จากค่าใน .env — ทำครั้งเดียว

ใช้ GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET ชุดเดียวกับที่เชื่อม Calendar ได้ แต่ต้องเพิ่ม redirect URI นี้ใน
Google Cloud Console (Credentials -> OAuth 2.0 Client -> Authorized redirect URIs):
    http://localhost:8501/oauth2callback

รัน:  venv\\Scripts\\python tools\\make_streamlit_secrets.py [--force]
ไฟล์ที่ได้ถูก ignore จาก git แล้ว (มีความลับ) สคริปต์นี้ไม่แสดงค่าลับออกหน้าจอ
"""

import argparse
import os
import pathlib
import secrets
import sys

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / ".streamlit" / "secrets.toml"


def toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="เขียนทับไฟล์เดิม (จะสร้าง cookie_secret ใหม่ ทุกคนต้องล็อกอินใหม่)")
    ap.add_argument("--port", default="8501")
    args = ap.parse_args()

    load_dotenv(ROOT / ".env")
    client_id, client_secret = os.environ.get("GOOGLE_CLIENT_ID", ""), os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        sys.exit("ไม่พบ GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET ใน .env — ใส่ก่อนแล้วรันใหม่")
    if TARGET.exists() and not args.force:
        print(f"มีไฟล์อยู่แล้ว: {TARGET} (ใช้ --force ถ้าต้องการสร้างใหม่)")
        return
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(
        "[auth]\n"
        f"redirect_uri = {toml_str(f'http://localhost:{args.port}/oauth2callback')}\n"
        f"cookie_secret = {toml_str(secrets.token_urlsafe(48))}\n"
        f"client_id = {toml_str(client_id)}\n"
        f"client_secret = {toml_str(client_secret)}\n"
        'server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"\n',
        encoding="utf-8",
    )
    print(f"สร้างแล้ว: {TARGET}")
    print(f"อย่าลืมเพิ่ม Authorized redirect URI ใน Google Cloud Console:  http://localhost:{args.port}/oauth2callback")


if __name__ == "__main__":
    main()
