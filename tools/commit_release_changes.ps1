param(
    [Parameter(Mandatory = $true)]
    [string]$BuildId
)

$ErrorActionPreference = "Stop"

# Stage tracked updates/deletes.
git add -u -- .
if ($LASTEXITCODE -ne 0) { throw "git add -u failed" }

# Stage new source/docs/scripts (respect .gitignore; never force data/).
$extraGlobs = @(
    "app/",
    "web/src/",
    "web/index.html",
    "web/package.json",
    "web/package-lock.json",
    "web/tsconfig.json",
    "web/vite.config.*",
    "tools/",
    "docs/",
    "*.bat",
    "*.spec",
    "*.md",
    "requirements.txt",
    "AGENTS.md"
)
foreach ($g in $extraGlobs) {
    git add -- $g 2>$null
}

$status = @(git status --porcelain)
if (-not $status -or $status.Count -eq 0) {
    Write-Host "No pending changes to commit."
    exit 0
}

$files = @()
foreach ($line in $status) {
    if (-not $line -or $line.Length -lt 4) { continue }
    $path = $line.Substring(3).Trim()
    if ($path -match " -> ") {
        $path = ($path -split " -> ", 2)[1]
    }
    if ($path) { $files += $path }
}
$files = $files | Select-Object -Unique | Sort-Object

$maxFiles = 40
$fileBlock = if ($files.Count -eq 0) {
    "(see git status)"
} elseif ($files.Count -le $maxFiles) {
    ($files | ForEach-Object { "- $_" }) -join "`n"
} else {
    $head = ($files | Select-Object -First $maxFiles | ForEach-Object { "- $_" }) -join "`n"
    "$head`n- ... and $($files.Count - $maxFiles) more"
}

$message = @"
build $BuildId

Changed:
$fileBlock
"@

$tmp = [System.IO.Path]::GetTempFileName()
try {
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tmp, $message.Replace("`r`n", "`n").Replace("`n", "`r`n"), $utf8)
    git commit -F $tmp
    if ($LASTEXITCODE -ne 0) { throw "git commit failed" }
    Write-Host "Committed $($files.Count) path(s) for build $BuildId"
} finally {
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
}
