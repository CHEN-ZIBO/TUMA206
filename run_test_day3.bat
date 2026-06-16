@echo off
cd /d "%~dp0"
echo ============================================================
echo  Day 3 Tests — PLC Controller
echo ============================================================
echo.
"D:\ProgramData\Python\python.exe" tests\test_plc_controller.py
echo.
echo ============================================================
echo  Done. Chart saved to tests\charts\day3_plc_controller.png
echo ============================================================
pause
