"""
แบบฟอร์มรายงานการประชุม (template) — แก้ที่ไฟล์นี้ไฟล์เดียวเมื่อได้แบบฟอร์มจริงจากอาจารย์/คณะ
ไม่ต้องแตะโค้ดวาด PDF (pdf_report.py) หรือโค้ด AI

  - labels:    ข้อความหัวข้อ/คำนำหน้าทุกจุดในรายงาน
  - sections:  ลำดับส่วนของรายงานจากบนลงล่าง (ลบ/สลับลำดับได้) ค่าที่ใช้ได้:
               header, attendees, absent, opening, agenda, other_matters, closing, summary, action_items, signature
  - signatures: ช่องลงชื่อท้ายรายงาน (role = chair/secretary เพื่อดึงชื่อจากรายชื่อผู้เข้าร่วมมาใส่ในวงเล็บ)
  - approval_note: ข้อความแสดงการอนุมัติท้ายรายงาน ({name} {date})
  - draft_watermark: ลายน้ำของรายงานที่ยังไม่อนุมัติ

ตอนนี้เป็นโครงมาตรฐานทั่วไปของรายงานการประชุมไทย ยังไม่ใช่แบบฟอร์มเฉพาะหน่วยงาน
"""

TEMPLATE = {
    "doc_title": "รายงานการประชุม",
    "labels": {
        "meeting_no": "ครั้งที่",
        "date": "เมื่อวันที่",
        "time": "เวลา",
        "time_unit": "น.",
        "venue": "ณ",
        "attendees": "ผู้มาประชุม",
        "absent": "ผู้ไม่มาประชุม",
        "none": "— ไม่มี —",
        "chair_suffix": "ประธานในที่ประชุม",
        "secretary_suffix": "เลขานุการ",
        "opening": "เริ่มประชุมเวลา",
        "closing": "เลิกประชุมเวลา",
        "agenda": "ระเบียบวาระ",
        "agenda_item": "วาระที่",
        "discussion": "สาระสำคัญ",
        "resolution": "มติที่ประชุม",
        "no_resolution": "เพื่อทราบ / ยังไม่มีมติ",
        "other_matters": "เรื่องอื่น ๆ",
        "summary": "สรุปภาพรวมการประชุม",
        "action_items": "งานที่ได้รับมอบหมาย",
        "col_no": "ลำดับ",
        "col_task": "งาน / นัดหมาย",
        "col_who": "ผู้รับผิดชอบ",
        "col_when": "กำหนด",
        "no_action_items": "— ไม่มีงานที่ได้รับมอบหมาย —",
    },
    "sections": [
        "header", "attendees", "absent", "opening", "agenda", "other_matters",
        "closing", "summary", "action_items", "signature",
    ],
    "signatures": [
        {"label": "ผู้จดรายงานการประชุม", "role": "secretary"},
        {"label": "ผู้ตรวจรายงานการประชุม", "role": "chair"},
    ],
    "approval_note": "รายงานนี้ได้รับการอนุมัติโดย {name} เมื่อ {date}",
    "draft_watermark": "ฉบับร่าง",
}
