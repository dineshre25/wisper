@echo off
REM Kills the currently running Flow instance, if any, using flow.lock.
REM Triggered by Task Scheduler on workstation lock.

setlocal
set "LOCKFILE=%~dp0flow.lock"

if exist "%LOCKFILE%" (
    for /f "usebackq delims=" %%p in ("%LOCKFILE%") do set PID=%%p
    taskkill /PID %PID% /F >nul 2>&1
    del "%LOCKFILE%" >nul 2>&1
)
