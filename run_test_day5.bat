@echo off
cd /d "%~dp0"
echo ============================================================
echo  Day 5 Tests — MQTT Client
echo ============================================================
echo.
"D:\ProgramData\Python\python.exe" tests\test_mqtt_client.py
echo.
echo ============================================================
echo  Done. Chart saved to tests\charts\day5_mqtt_client.png
echo ============================================================
pause
