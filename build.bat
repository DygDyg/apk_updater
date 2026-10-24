@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Optional: build.bat nopause  — skip pause/explorer (used by publish_release.bat)
set "NOPAUSE=0"
if /I "%~1"=="nopause" set "NOPAUSE=1"

echo === APK Updater build ===
echo.

echo Closing running APK Updater if any...
taskkill /F /IM APKUpdater.exe >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq APK Updater*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq APKUpdater*" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\close_app.ps1" 2>nul
timeout /t 1 /nobreak >nul

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
  set "PIP=.venv\Scripts\pip.exe"
) else (
  set "PY=python"
  set "PIP=pip"
)

echo Using: %PY%
"%PY%" -c "import sys; print(sys.version)"
if errorlevel 1 (
  echo ERROR: Python not found. Create .venv or install Python.
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo ERROR: npm not found. Install Node.js to build the web UI.
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)

echo.
echo Building web UI...
pushd web
call npm install
if errorlevel 1 (
  echo ERROR: npm install failed.
  popd
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)
call npm run build
if errorlevel 1 (
  echo ERROR: npm run build failed.
  popd
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)
popd

if not exist "web\dist\index.html" (
  echo ERROR: web\dist\index.html missing after build.
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)

echo.
echo Installing build deps...
"%PIP%" install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo ERROR: pip install failed.
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)

echo.
echo Building with PyInstaller...
"%PY%" -m PyInstaller --noconfirm --clean APKUpdater.spec
if errorlevel 1 (
  echo.
  echo ERROR: PyInstaller build failed.
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)

if not exist "dist\APKUpdater\APKUpdater.exe" (
  echo ERROR: dist\APKUpdater\APKUpdater.exe missing.
  if "%NOPAUSE%"=="0" pause
  exit /b 1
)

echo.
echo Build OK.
echo EXE: "%~dp0dist\APKUpdater\APKUpdater.exe"
if "%NOPAUSE%"=="0" (
  explorer "%~dp0dist\APKUpdater"
  pause
)
exit /b 0
