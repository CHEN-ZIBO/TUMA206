@echo off
cd /d "%~dp0"
echo ============================================================
echo  Day 2 Tests — Process Simulator + Sensor Simulator
echo ============================================================
echo.
echo [1/2] Running test_process_simulator.py ...
echo.
"D:\ProgramData\Python\python.exe" tests\test_process_simulator.py
echo.
echo [2/2] Running test_sensor_simulator.py ...
echo.
"D:\ProgramData\Python\python.exe" tests\test_sensor_simulator.py
echo.
echo ============================================================
echo  Done. Charts saved to tests\charts\
echo  day2_process_simulator.png
echo  day2_sensor_simulator.png
echo ============================================================
pause
