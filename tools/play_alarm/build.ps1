# Build play_alarm.dex (USAGE_ALARM beep helper for locked phones)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Root = (Resolve-Path (Join-Path $Here "..\..")).Path
$OutDir = Join-Path $Here "out"
$DexOut = Join-Path $Root "app\resources\play_alarm.dex"
$Sdk = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$AndroidJar = Join-Path $Sdk "platforms\android-34\android.jar"
$D8 = Join-Path $Sdk "build-tools\35.0.1\d8.bat"

if (-not (Test-Path $AndroidJar)) {
    throw "android.jar not found: $AndroidJar"
}
if (-not (Test-Path $D8)) {
    $found = Get-ChildItem (Join-Path $Sdk "build-tools") -Recurse -Filter d8.bat -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $found) { throw "d8.bat not found under Android SDK build-tools" }
    $D8 = $found
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path $DexOut) | Out-Null

& javac -classpath $AndroidJar -source 11 -target 11 -encoding UTF-8 -d $OutDir (Join-Path $Here "PlayAlarm.java")
if ($LASTEXITCODE -ne 0) { throw "javac failed" }

& $D8 --lib $AndroidJar --min-api 26 --output $OutDir (Join-Path $OutDir "PlayAlarm.class")
if ($LASTEXITCODE -ne 0) { throw "d8 failed" }

Copy-Item -Force (Join-Path $OutDir "classes.dex") $DexOut
Write-Host "Wrote $DexOut ($((Get-Item $DexOut).Length) bytes)"
