@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === APK Updater setup ===
echo.

REM Prefer py launcher, then python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
  )
)
if not defined PY (
  echo ERROR: Python 3.10+ not found.
  echo Install from https://www.python.org/downloads/
  echo Enable: "Add python.exe to PATH"
  pause
  exit /b 1
)

echo Python:
%PY% -c "import sys; print(sys.version)"
echo.

set "NEED_VENV=0"
if not exist ".venv\Scripts\python.exe" (
  set "NEED_VENV=1"
) else (
  REM Broken after Python upgrade/move: stub still points at old home
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo .venv is broken ^(old Python path^) — recreating ...
    set "NEED_VENV=1"
  )
)

if "%NEED_VENV%"=="1" (
  if exist ".venv" (
    echo Removing old .venv ...
    rmdir /s /q ".venv" 2>nul
    if exist ".venv" (
      echo ERROR: cannot delete .venv — close the app / run_apk_updater.bat and retry.
      pause
      exit /b 1
    )
  )
  echo Creating .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo ERROR: venv create failed.
    pause
    exit /b 1
  )
) else (
  echo .venv OK
)

set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo ERROR: .venv\Scripts\python.exe missing.
  pause
  exit /b 1
)

echo Upgrading pip ...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 (
  echo ERROR: pip upgrade failed.
  pause
  exit /b 1
)

echo Installing Python packages from requirements.txt ...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: pip install failed.
  pause
  exit /b 1
)

echo.
if exist "web\dist\index.html" (
  echo web\dist OK — frontend already built
) else (
  echo web\dist missing — need Node.js / npm to build UI
  where npm >nul 2>&1
  if errorlevel 1 (
    echo ERROR: npm not found.
    echo Install Node.js LTS from https://nodejs.org/
    echo Or copy web\dist from another PC that already built it.
    pause
    exit /b 1
  )
  pushd web
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
  if not exist "web\dist\index.html" (
    echo ERROR: web\dist still missing after build.
    pause
    exit /b 1
  )
  echo web\dist built OK
)

echo.
echo Setup OK.
echo Run: run_apk_updater.bat
echo Stop: stop.bat
echo.
pause
exit /b 0
