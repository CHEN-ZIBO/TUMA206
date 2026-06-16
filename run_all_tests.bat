@echo off
cd /d "%~dp0"
set PY="D:\ProgramData\Python\python.exe"
set PASS=0
set FAIL=0

echo ============================================================
echo  Water Treatment Digital Twin — Full Test Suite
echo ============================================================
echo.

echo [Day 2 - 1/5] test_process_simulator.py
%PY% tests\test_process_simulator.py
if %errorlevel% neq 0 ( set /a FAIL+=1 ) else ( set /a PASS+=1 )
echo.

echo [Day 2 - 2/5] test_sensor_simulator.py
%PY% tests\test_sensor_simulator.py
if %errorlevel% neq 0 ( set /a FAIL+=1 ) else ( set /a PASS+=1 )
echo.

echo [Day 3 - 3/5] test_plc_controller.py
%PY% tests\test_plc_controller.py
if %errorlevel% neq 0 ( set /a FAIL+=1 ) else ( set /a PASS+=1 )
echo.

echo [Day 4 - 4/5] test_historian.py
%PY% tests\test_historian.py
if %errorlevel% neq 0 ( set /a FAIL+=1 ) else ( set /a PASS+=1 )
echo.

echo [Day 5 - 5/5] test_mqtt_client.py
%PY% tests\test_mqtt_client.py
if %errorlevel% neq 0 ( set /a FAIL+=1 ) else ( set /a PASS+=1 )
echo.

echo ============================================================
echo  Results: %PASS% test files passed, %FAIL% failed
echo  Charts:  tests\charts\
echo ============================================================
pause
