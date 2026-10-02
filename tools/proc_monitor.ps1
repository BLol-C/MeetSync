# วัดหน่วยความจำของ Python (uvicorn) และ Chrome ของบอท ทุก N วินาที เก็บเป็น CSV ไว้เป็นหลักฐานทดสอบรัน 1 ชั่วโมงขึ้นไป
#
# วิธีใช้ (เปิดอีกหน้าต่าง PowerShell ระหว่างที่บอทกำลังอยู่ในห้องประชุมจริง):
#   powershell -ExecutionPolicy Bypass -File tools\proc_monitor.ps1 -Minutes 95
#   (ไม่ใส่ -Minutes = วัดจนกด Ctrl+C)
#
# ผลลัพธ์: soak_<เวลา>.csv ในโฟลเดอร์ปัจจุบัน — คอลัมน์ time, python_mb, chrome_mb, chrome_procs, total_mb
# จากนั้นสรุปแนวโน้มด้วย:  powershell -File tools\proc_monitor.ps1 -Summarize soak_xxx.csv
#
# เกณฑ์ผ่านที่เสนอ: total_mb ช่วง 1/4 สุดท้ายต้องไม่สูงกว่าช่วง 1/4 แรกเกิน ~15% และไม่มีขั้นบันไดที่เพิ่มขึ้นเรื่อยๆ
param(
    [int]$IntervalSeconds = 60,
    [double]$Minutes = 0,
    [string]$Summarize = ""
)

if ($Summarize) {
    $rows = Import-Csv $Summarize
    if ($rows.Count -lt 8) { Write-Host "ข้อมูลน้อยเกินไปที่จะสรุป ($($rows.Count) แถว)"; exit 1 }
    $q = [math]::Floor($rows.Count / 4)
    $first = ($rows | Select-Object -First $q | Measure-Object total_mb -Average).Average
    $last = ($rows | Select-Object -Last $q | Measure-Object total_mb -Average).Average
    $peak = ($rows | Measure-Object total_mb -Maximum).Maximum
    $minutes = [math]::Round(($rows.Count * $IntervalSeconds) / 60)
    $growth = if ($first -gt 0) { ($last - $first) / $first * 100 } else { 0 }
    Write-Host ("ช่วงเวลาที่วัด: ~{0} นาที ({1} ตัวอย่าง)" -f $minutes, $rows.Count)
    Write-Host ("หน่วยความจำรวมเฉลี่ย 1/4 แรก: {0:N0} MB  ->  1/4 สุดท้าย: {1:N0} MB  (เปลี่ยน {2:+0.0;-0.0}%)" -f $first, $last, $growth)
    Write-Host ("ค่าสูงสุด: {0:N0} MB" -f $peak)
    if ($growth -le 15) { Write-Host "ผล: ผ่าน (ไม่พบแนวโน้มรั่ว)" -ForegroundColor Green } else { Write-Host "ผล: น่าสงสัย — หน่วยความจำโตเกิน 15% ตรวจสอบกราฟ" -ForegroundColor Yellow }
    exit 0
}

$file = "soak_{0:yyyyMMdd-HHmmss}.csv" -f (Get-Date)
"time,python_mb,chrome_mb,chrome_procs,total_mb" | Out-File $file -Encoding utf8
$end = if ($Minutes -gt 0) { (Get-Date).AddMinutes($Minutes) } else { [datetime]::MaxValue }
Write-Host "บันทึกลง $file ทุก $IntervalSeconds วินาที (Ctrl+C เพื่อหยุด)"

while ((Get-Date) -lt $end) {
    $py = Get-Process python -ErrorAction SilentlyContinue
    # Chrome ที่ Playwright เปิด (ชื่อโปรเซสเป็น chrome หรือ chromium) — รวมทุก process ลูก (GPU/renderer)
    $ch = Get-Process chrome, chromium -ErrorAction SilentlyContinue
    $pyMb = [math]::Round((($py | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 1)
    $chMb = [math]::Round((($ch | Measure-Object WorkingSet64 -Sum).Sum) / 1MB, 1)
    $n = ($ch | Measure-Object).Count
    $line = "{0:HH:mm:ss},{1},{2},{3},{4}" -f (Get-Date), $pyMb, $chMb, $n, ($pyMb + $chMb)
    $line | Out-File $file -Append -Encoding utf8
    Write-Host $line
    Start-Sleep -Seconds $IntervalSeconds
}
Write-Host "ครบเวลาแล้ว — สรุปด้วย: powershell -File tools\proc_monitor.ps1 -Summarize $file"
