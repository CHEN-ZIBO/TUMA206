@echo off
cd /d "%~dp0"
echo ============================================================
echo  Day 4 Tests — Historian
echo ============================================================
echo.
"D:\ProgramData\Python\python.exe" tests\test_historian.py
echo.
echo ============================================================
echo  Done. Chart saved to tests\charts\day4_historian.png
echo ============================================================
pause
