@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Stopping APK Updater...

REM Kill by PID file if present
if exist "data\apk_updater.pid" (
  set /p PID=<"data\apk_updater.pid"
  if defined PID (
    taskkill /F /PID %PID% >nul 2>&1
  )
  del /f /q "data\apk_updater.pid" >nul 2>&1
)

REM Kill listeners on port 8765
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  echo Killing PID %%P on :8765
  taskkill /F /PID %%P >nul 2>&1
)

REM Kill python/pythonw running this project's main.py
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = (Resolve-Path '%~dp0').Path; Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -and ($_.CommandLine -like ('*'+$root+'*main.py*') -or $_.CommandLine -like '*apk_updater*main.py*') } | ForEach-Object { Write-Host ('Killing PID '+$_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done. Tray icon / server should be gone.
pause
exit /b 0
