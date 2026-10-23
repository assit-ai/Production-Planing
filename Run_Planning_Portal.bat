@echo off
title Paragon Production & Store Planning Portal
color 0B
echo =====================================================================
echo    PARAGON AGRO LIMITED - PRODUCTION & STORE PLANNING SYSTEM
echo    Web Portal & Mobile Responsive App
echo =====================================================================
echo.
echo Starting Flask Server on http://localhost:5055 ...
echo Mobile/Local Network URL: http://0.0.0.0:5055
echo.

:: Automatically open browser after 2 seconds
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:5055"

cd /d "%~dp0portal\backend"
python app.py
pause
