@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM Deploy release to the server share.
REM Order: build -> stop remote (delayed start) -> wait until down -> copy -> wait until up.
REM Keeps server data\ and .venv untouched.
REM ASCII-only body (cmd.exe UTF-8-safe).
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"
set "DST=Z:\apk_updater"
set "REMOTE_URL=http://192.168.1.150:8765"
if not "%APK_UPDATER_REMOTE%"=="" set "REMOTE_URL=%APK_UPDATER_REMOTE%"
REM Seconds the remote helper waits before starting again (must cover copy time)
set "RESTART_DELAY=45"

echo === Deploy APK Updater release ===
echo Source: %SRC%
echo Dest:   %DST%
echo Remote: %REMOTE_URL%
echo.

REM Do not run this bat from Z: (server share) — Source would equal Dest.
if /I "%~d0"=="Z:" (
  echo.
  echo ERROR: deploy_release.bat was started from Z: ^(server share^).
  echo Run it from the LOCAL folder, e.g. E:\python\apk_updater\deploy_release.bat
  echo.
  pause
  exit /b 1
)
if /I "%SRC%"=="%DST%" (
  echo.
  echo ERROR: Source and Dest are the same. Run deploy from the LOCAL project folder.
  echo.
  pause
  exit /b 1
)

if not exist "%DST%\" (
  echo ERROR: "%DST%" not found.
  echo Map the network drive Z: and make sure the folder exists.
  pause
  exit /b 1
)

REM Build web UI so web\dist is up to date
where npm >nul 2>&1
if errorlevel 1 (
  echo ERROR: npm not found. Install Node.js to build the web UI.
  pause
  exit /b 1
)

echo Building web UI...
pushd "%SRC%\web"
call npm install
if errorlevel 1 (
  echo ERROR: npm install failed.
  popd
  pause
  exit /b 1
)
call npm run build
if errorlevel 1 (
  echo ERROR: npm run build failed.
  popd
  pause
  exit /b 1
)
popd

if not exist "%SRC%\web\dist\index.html" (
  echo ERROR: web\dist\index.html missing after build.
  pause
  exit /b 1
)

echo.
echo Stopping remote app first (files unlock; start in %RESTART_DELAY%s)...
echo   GET %REMOTE_URL%/api/restart?key=restart^&delay=%RESTART_DELAY%
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $r = Invoke-WebRequest -UseBasicParsing ('%REMOTE_URL%/api/restart?key=restart&delay=%RESTART_DELAY%') -TimeoutSec 15; Write-Host $r.Content } catch { Write-Host ('WARN: stop/restart call failed: ' + $_.Exception.Message); Write-Host 'Continuing with copy - start the app manually if needed.' }"

echo Waiting until remote is down...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$u='%REMOTE_URL%/api/status'; $down=$false; for($i=0; $i -lt 30; $i++){ try { $null=Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 2; Start-Sleep -Seconds 1 } catch { Write-Host ('OK - remote down (try ' + ($i+1) + ')'); $down=$true; break } }; if(-not $down){ Write-Host 'WARN: remote still answering - copy may hit locked files.' }; Start-Sleep -Seconds 2"

echo.
echo Copying files to %DST% ...
echo   (skipping: .venv, data, node_modules, __pycache__, .git, build)
echo.

set "RC=0"
set "FAIL_STEP="

call :RoboRetry "app" "%SRC%\app" "%DST%\app" "/E /XD __pycache__ .pytest_cache"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=app"
)

if exist "%SRC%\tools\" (
  call :RoboRetry "tools" "%SRC%\tools" "%DST%\tools" "/E"
  if errorlevel 1 (
    set "RC=1"
    set "FAIL_STEP=tools"
  )
)

REM Web sources + config (exclude node_modules and built dist; dist mirrored next)
call :RoboRetry "web" "%SRC%\web" "%DST%\web" "/E /XD node_modules dist"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=web"
)

REM Built UI: mirror so old hashed assets are removed
call :RoboRetry "web\dist" "%SRC%\web\dist" "%DST%\web\dist" "/MIR"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=web\dist"
)

call :CopyOne "main.py" "%SRC%\main.py" "%DST%\main.py"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=main.py"
)
call :CopyOne "requirements.txt" "%SRC%\requirements.txt" "%DST%\requirements.txt"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=requirements.txt"
)
if exist "%SRC%\README.md" call :CopyOne "README.md" "%SRC%\README.md" "%DST%\README.md"
if exist "%SRC%\.gitignore" call :CopyOne ".gitignore" "%SRC%\.gitignore" "%DST%\.gitignore"
call :CopyOne "setup.bat" "%SRC%\setup.bat" "%DST%\setup.bat"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=setup.bat"
)
call :CopyOne "stop.bat" "%SRC%\stop.bat" "%DST%\stop.bat"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=stop.bat"
)
call :CopyOne "run_apk_updater.bat" "%SRC%\run_apk_updater.bat" "%DST%\run_apk_updater.bat"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=run_apk_updater.bat"
)
if exist "%SRC%\build.bat" call :CopyOne "build.bat" "%SRC%\build.bat" "%DST%\build.bat"
call :CopyOne "deploy_release.bat" "%SRC%\deploy_release.bat" "%DST%\deploy_release.bat"
if errorlevel 1 (
  set "RC=1"
  set "FAIL_STEP=deploy_release.bat"
)

if not "%RC%"=="0" (
  echo.
  echo ERROR: copy failed at step: !FAIL_STEP!
  echo Check that nobody has files locked on %DST%.
  echo Tip: wait until remote is fully stopped, then re-run this bat.
  pause
  exit /b 1
)

echo.
echo OK - release copied to %DST%
echo Server data\ and .venv were left as-is.
echo If requirements.txt changed, run setup.bat on the server once.
echo.
echo Waiting for remote app to come back (%REMOTE_URL%/api/status)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$u='%REMOTE_URL%/api/status'; $ok=$false; for($i=0; $i -lt 50; $i++){ try { $r=Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 2; if($r.StatusCode -eq 200){ Write-Host ('OK - remote is up (try ' + ($i+1) + ')'); $ok=$true; break } } catch {} ; Start-Sleep -Seconds 2 }; if(-not $ok){ Write-Host 'WARN: remote did not answer in time. Start run_apk_updater.bat on that PC.' }"
echo.

pause
exit /b 0

REM ---- helpers ----
:RoboRetry
REM %1=label %2=src %3=dst %4=extra flags
set "LABEL=%~1"
set "RSRC=%~2"
set "RDST=%~3"
set "RFLAGS=%~4"
set "ATTEMPT=1"
:RoboRetryLoop
echo   [%LABEL%] attempt !ATTEMPT!...
robocopy "!RSRC!" "!RDST!" !RFLAGS! /R:2 /W:2 /NFL /NDL /NJH /NJS /NC /NS /NP
set "RE=!ERRORLEVEL!"
if !RE! LSS 8 (
  echo   [%LABEL%] ok ^(robocopy=!RE!^)
  exit /b 0
)
echo   [%LABEL%] robocopy errorlevel=!RE!
if !ATTEMPT! GEQ 3 (
  echo   [%LABEL%] FAILED after 3 attempts
  exit /b 1
)
set /a ATTEMPT+=1
timeout /t 2 /nobreak >nul
goto RoboRetryLoop

:CopyOne
REM %1=label %2=src %3=dst
set "CLABEL=%~1"
set "CSRC=%~2"
set "CDST=%~3"
if /I "!CSRC!"=="!CDST!" (
  echo   [%CLABEL%] skip ^(same path^)
  exit /b 0
)
set "CATTEMPT=1"
:CopyOneLoop
copy /Y "!CSRC!" "!CDST!" >nul
if not errorlevel 1 (
  echo   [%CLABEL%] ok
  exit /b 0
)
echo   [%CLABEL%] copy failed ^(attempt !CATTEMPT!^)
if !CATTEMPT! GEQ 3 (
  echo   [%CLABEL%] FAILED after 3 attempts
  exit /b 1
)
set /a CATTEMPT+=1
timeout /t 2 /nobreak >nul
goto CopyOneLoop
