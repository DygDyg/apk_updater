@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM Publish a new portable build to GitHub Releases.
REM ASCII-only body (cmd.exe UTF-8-safe). Requires: git, gh auth, Node, Python/.venv

echo === Publish APK Updater release ===
echo.

where git >nul 2>&1
if errorlevel 1 (
  echo ERROR: git not found in PATH.
  pause
  exit /b 1
)
where gh >nul 2>&1
if errorlevel 1 (
  echo ERROR: gh not found. Install GitHub CLI and run: gh auth login
  pause
  exit /b 1
)

gh auth status >nul 2>&1
if errorlevel 1 (
  echo ERROR: gh is not authenticated. Run: gh auth login
  pause
  exit /b 1
)

if not exist ".git\" (
  echo ERROR: Not a git repository. Init and push remote first.
  pause
  exit /b 1
)

for /f "delims=" %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "BUILD_ID=%%I"
if "%BUILD_ID%"=="" (
  echo ERROR: failed to generate BUILD_ID
  pause
  exit /b 1
)

set "TAG=build-%BUILD_ID%"
echo Build ID: %BUILD_ID%
echo Tag:      %TAG%
echo.

echo Writing app\build_info.py ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\write_build_info.ps1" -BuildId "%BUILD_ID%"
if errorlevel 1 (
  echo ERROR: failed to write build_info.py
  pause
  exit /b 1
)

echo Committing pending changes for this release...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\commit_release_changes.ps1" -BuildId "%BUILD_ID%"
if errorlevel 1 (
  echo ERROR: git commit of release changes failed.
  pause
  exit /b 1
)

for /f %%C in ('git status --porcelain ^| find /c /v ""') do set "DIRTY=%%C"
if not "%DIRTY%"=="0" (
  echo WARN: Working tree still has unstaged/untracked files after commit.
  echo       They will NOT be in the git tag; check git status.
)

echo.
echo Creating tag %TAG% ...
git tag "%TAG%"
if errorlevel 1 (
  echo ERROR: git tag failed ^(tag may already exist^).
  pause
  exit /b 1
)

echo Pushing main and tag...
git push origin HEAD
if errorlevel 1 (
  echo ERROR: git push failed.
  pause
  exit /b 1
)
git push origin "%TAG%"
if errorlevel 1 (
  echo ERROR: git push tag failed.
  pause
  exit /b 1
)

echo.
echo Building portable EXE...
call "%~dp0build.bat" nopause
if errorlevel 1 (
  echo ERROR: build.bat failed.
  pause
  exit /b 1
)

set "ZIP=%~dp0APKUpdater-windows-x64.zip"
if exist "%ZIP%" del /f /q "%ZIP%" >nul 2>&1

echo.
echo Zipping dist\APKUpdater ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path (Join-Path '%~dp0' 'dist\APKUpdater\*') -DestinationPath '%ZIP%' -Force"
if errorlevel 1 (
  echo ERROR: zip failed.
  pause
  exit /b 1
)
if not exist "%ZIP%" (
  echo ERROR: zip file missing.
  pause
  exit /b 1
)

echo.
echo Writing release notes from git history...
set "NOTES=%~dp0.release_notes.md"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\write_release_notes.ps1" -Tag "%TAG%" -BuildId "%BUILD_ID%" -OutFile "%NOTES%"
if errorlevel 1 (
  echo ERROR: failed to write release notes
  pause
  exit /b 1
)

echo.
echo Creating GitHub Release %TAG% ...
gh release create "%TAG%" "%ZIP%" --title "Build %BUILD_ID%" --notes-file "%NOTES%" --latest
if errorlevel 1 (
  echo ERROR: gh release create failed.
  pause
  exit /b 1
)

del /f /q "%NOTES%" >nul 2>&1

echo.
echo OK - published %TAG%
echo Asset: APKUpdater-windows-x64.zip
echo Release notes include commits since the previous build-* tag.
echo.
pause
exit /b 0
