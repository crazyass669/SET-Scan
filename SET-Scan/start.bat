@echo off
cd /d "%~dp0"
title SET Dashboard

echo ============================================
echo   SET Dashboard
echo ============================================
echo.

REM ตรวจว่ามี Flask ติดตั้งแล้วหรือยัง
python -c "import flask" 2>nul
if errorlevel 1 (
    echo [!] ยังไม่ได้ติดตั้ง dependencies
    echo [*] กำลังติดตั้ง...
    pip install -r requirements.txt
    echo.
)

REM เปิด browser หลัง 2 วินาที
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5000"

REM เริ่ม Flask server
python app.py

pause
