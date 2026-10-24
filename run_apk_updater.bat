@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "web\dist\index.html" (
  echo web\dist missing — building frontend...
  where npm >nul 2>&1
  if errorlevel 1 (
    echo ERROR: npm not found. Run: cd web ^&^& npm install ^&^& npm run build
    pause
    exit /b 1
  )
  pushd web
  call npm install
  call npm run build
  popd
  if not exist "web\dist\index.html" (
    echo ERROR: frontend build failed.
    pause
    exit /b 1
  )
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
  set "LAUNCH=%~dp0.venv\Scripts\pythonw.exe"
) else if exist "%~dp0.venv\Scripts\python.exe" (
  set "LAUNCH=%~dp0.venv\Scripts\python.exe"
) else (
  set "LAUNCH=pythonw"
)

REM Listen on 0.0.0.0:8765 (LAN). Browser still opens 127.0.0.1.
echo Starting APK Updater
echo   This PC:  http://127.0.0.1:8765
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ip=''; try { $s=New-Object Net.Sockets.Socket([Net.Sockets.AddressFamily]::InterNetwork,'Dgram','Udp'); $s.Connect('8.8.8.8',80); $ip=$s.LocalEndPoint.Address.ToString(); $s.Close() } catch {}; if ($ip) { Write-Host ('  LAN:     http://'+$ip+':8765') }"
echo Tray icon: teal "AU"  (may be under ^ hidden icons)
echo Stop: stop.bat
echo.

REM Allow inbound TCP 8765 for LAN clients (ignore errors if no admin)
netsh advfirewall firewall delete rule name="APK Updater 8765" >nul 2>&1
netsh advfirewall firewall add rule name="APK Updater 8765" dir=in action=allow protocol=TCP localport=8765 >nul 2>&1

start "APK Updater" /D "%~dp0" "%LAUNCH%" "%~dp0main.py"
timeout /t 2 /nobreak >nul

REM Quick health check; if failed show log / console fallback
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/status -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if errorlevel 1 (
  echo.
  echo WARNING: server not responding yet.
  if exist "%~dp0data\startup_error.log" (
    echo --- startup_error.log (last 40 lines) ---
    powershell -NoProfile -Command "Get-Content -LiteralPath '%~dp0data\startup_error.log' -Tail 40 -ErrorAction SilentlyContinue"
  )
  echo.
  echo Retrying with visible console...
  if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0main.py" --console
  ) else (
    python "%~dp0main.py" --console
  )
  pause
  exit /b 1
)

echo OK — listening on 0.0.0.0:8765 (localhost + LAN)
exit /b 0
