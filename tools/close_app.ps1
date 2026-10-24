# Close APK Updater python processes for this repo before PyInstaller build.
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root 'main.py'))) {
  $root = $PSScriptRoot
}
$marker = [regex]::Escape((Join-Path $root 'main.py'))
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -and ($_.CommandLine -match $marker) } |
  ForEach-Object {
    Write-Host ("Stopping PID {0}" -f $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force
  }