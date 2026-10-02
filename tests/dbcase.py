"""ตัวช่วยร่วมของเทสต์ที่ต้องใช้ MySQL: สร้างฐานข้อมูลชั่วคราว meetsync_test_<สุ่ม> ต่อหนึ่งคลาสเทสต์ แล้วลบทิ้งตอนจบ
ไม่แตะฐานข้อมูลจริงของแอป (DB_NAME ใน .env) ถ้าต่อ MySQL ไม่ได้ ทุกเทสต์ที่ใช้คลาสนี้จะถูกข้าม (skip)
"""

import pathlib
import sys
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import pymysql  # noqa: E402

import db  # noqa: E402


def server_conn():
    return pymysql.connect(
        host=db.DB_HOST, port=db.DB_PORT, user=db.DB_USER, password=db.DB_PASSWORD,
        autocommit=True, connect_timeout=5,
    )


class TempDbCase(unittest.TestCase):
    """สร้างฐานข้อมูลชั่วคราวหนึ่งก้อนต่อหนึ่งคลาสเทสต์ แล้วชี้ db.DB_NAME ไปที่มัน"""

    @classmethod
    def setUpClass(cls):
        try:
            conn = server_conn()
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"ต่อ MySQL ไม่ได้: {e!r}")
        cls.dbname = f"meetsync_test_{uuid.uuid4().hex[:8]}"
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{cls.dbname}` CHARACTER SET utf8mb4")
        conn.close()
        cls._orig_name = db.DB_NAME
        db.DB_NAME = cls.dbname

    @classmethod
    def tearDownClass(cls):
        db.DB_NAME = cls._orig_name
        conn = server_conn()
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{cls.dbname}`")
        conn.close()

    def rows(self, sql, args=()):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        finally:
            conn.close()

    def execute(self, sql, args=()):
        conn = db.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
        finally:
            conn.close()
