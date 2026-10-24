param(
    [Parameter(Mandatory = $true)]
    [string]$Tag,
    [Parameter(Mandatory = $true)]
    [string]$BuildId,
    [Parameter(Mandatory = $true)]
    [string]$OutFile
)

$ErrorActionPreference = "Stop"

function Get-PreviousBuildTag {
    param([string]$Current)
    $tags = @(git tag -l "build-*" --sort=-creatordate 2>$null)
    if (-not $tags) { return $null }
    foreach ($t in $tags) {
        if ($t -and $t -ne $Current) { return $t }
    }
    return $null
}

$prev = Get-PreviousBuildTag -Current $Tag
$commitLines = @()

if ($prev) {
    $rangeLabel = $prev
    $sectionTitle = "Changes since ``$prev``"
    $raw = @(git log "$prev..$Tag" --pretty=format:"%h%x09%s" --no-merges 2>$null)
    $stat = @(git diff --stat "$prev" "$Tag" 2>$null)
    $bodies = @(git log "$prev..$Tag" --pretty=format:"%B---SPLIT---" --no-merges 2>$null) -join "`n"
} else {
    $rangeLabel = "(no previous build tag)"
    $sectionTitle = "Recent commits"
    $raw = @(git log $Tag --pretty=format:"%h%x09%s" --no-merges -n 40 2>$null)
    $stat = @(git show --stat --format="" $Tag 2>$null)
    $bodies = @(git log $Tag --pretty=format:"%B---SPLIT---" --no-merges -n 5 2>$null) -join "`n"
}

foreach ($line in $raw) {
    if (-not $line) { continue }
    $tab = $line.IndexOf("`t")
    if ($tab -lt 0) { continue }
    $hash = $line.Substring(0, $tab).Trim()
    $subject = $line.Substring($tab + 1).Trim()
    if (-not $subject) { continue }
    $commitLines += "- $subject ($hash)"
}

$bodyBits = @()
foreach ($chunk in ($bodies -split "---SPLIT---")) {
    if ($chunk -match "(?ms)^Changed:\s*(.+)$") {
        $block = $Matches[1].Trim()
        if (-not $block) { continue }
        foreach ($fl in ($block -split "`r?`n")) {
            $fl = $fl.Trim()
            if ($fl -match '^-\s+\S') { $bodyBits += $fl }
        }
    }
}
$bodyBits = @($bodyBits | Select-Object -Unique)

if ($commitLines.Count -eq 0) {
    $changesBlock = "_No commits in range ($rangeLabel -> $Tag)._"
} else {
    $changesBlock = ($commitLines -join "`n")
}

$filesBlock = ""
if ($bodyBits.Count -gt 0) {
    $filesBlock = @"

## Files

$($bodyBits -join "`n")
"@
} elseif ($stat -and (@($stat | Where-Object { $_ -and $_.Trim() })).Count -gt 0) {
    $statText = (@($stat | Where-Object { $_ -and $_.Trim() })) -join "`n"
    $fence = '```'
    $filesBlock = @"

## Diffstat

$fence
$statText
$fence
"@
}

$notes = @"
Portable Windows build **$BuildId**

## $sectionTitle

$changesBlock
$filesBlock

## Build

- Tag: ``$Tag``
- Asset: ``APKUpdater-windows-x64.zip``
"@

$dir = Split-Path -Parent $OutFile
if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText(
    $OutFile,
    $notes.Replace("`r`n", "`n").Replace("`n", "`r`n"),
    $utf8
)
Write-Host "Wrote release notes: $OutFile ($($commitLines.Count) commits, $($bodyBits.Count) file lines)"
