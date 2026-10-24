param(
    [Parameter(Mandatory = $true)]
    [string]$BuildId,
    [string]$OutPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $OutPath) {
    $OutPath = Join-Path (Split-Path $PSScriptRoot -Parent) "app\build_info.py"
}

$content = @"
"""Build stamp written by publish_release.bat before packaging.

Format: YYYYMMDD-HHMMSS (local time). Placeholder ``dev`` until first publish.
"""

BUILD_ID = "$BuildId"
"@

[System.IO.File]::WriteAllText($OutPath, $content.Replace("`n", "`r`n"))
Write-Host "Wrote $OutPath BUILD_ID=$BuildId"
