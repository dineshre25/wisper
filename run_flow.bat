@echo off
REM Flow-Local launcher
REM Double-click this file to start Flow. No console window stays open.

cd /d "%~dp0"
call venv\Scripts\activate.bat
start "" /B pythonw.exe flow.py > flow_log.txt 2>&1
