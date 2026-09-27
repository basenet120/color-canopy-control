@echo off
rem Color Canopy workshop - Windows starter. Double-click, or: start.bat sim / allow / setup / status
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0workshop\start.ps1" %*
pause
