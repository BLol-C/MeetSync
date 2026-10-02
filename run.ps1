# เปิด MeetSync ทั้งสองส่วนพร้อมกัน: บริการบอท (FastAPI, พอร์ต 8000) + หน้าเว็บ (Streamlit, พอร์ต 8501)
#   powershell -ExecutionPolicy Bypass -File run.ps1
# ปิดด้วย Ctrl+C ในหน้าต่างนี้ (บริการบอทจะถูกปิดตามไปด้วย)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "ไม่พบ venv\Scripts\python.exe — สร้าง venv และติดตั้ง requirements.txt ก่อน" -ForegroundColor Red; exit 1 }

if (-not (Test-Path ".streamlit\secrets.toml")) {
    Write-Host "ยังไม่มี .streamlit\secrets.toml — สร้างจากค่าใน .env ให้ (ทำครั้งเดียว)" -ForegroundColor Yellow
    & $py tools\make_streamlit_secrets.py
}

Write-Host "เปิดบริการบอท http://127.0.0.1:8000 (หน้าต่างแยก — ห้ามปิดระหว่างประชุม)" -ForegroundColor Cyan
$bot = Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000" `
    -WorkingDirectory $PSScriptRoot -PassThru
try {
    Write-Host "เปิดหน้าเว็บ http://localhost:8501" -ForegroundColor Cyan
    & $py -m streamlit run streamlit_app.py
}
finally {
    if ($bot -and -not $bot.HasExited) { Stop-Process -Id $bot.Id -Force }
}
