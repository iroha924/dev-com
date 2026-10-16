<#
.SYNOPSIS
  Install the hirata-codex skills, custom agents and global AGENTS.md for Codex on Windows.

.DESCRIPTION
  Copies
    skills\<name>\       -> $HOME\.agents\skills\<name>\
    agents\<name>.toml   -> <CODEX_HOME>\agents\<name>.toml   (CODEX_HOME defaults to $HOME\.codex)
    AGENTS.md            -> <CODEX_HOME>\AGENTS.md

  A target that already exists with different content is left alone unless -Force is given.
  With -Force the old copy is moved to $HOME\.hirata-codex-backup\<timestamp>\ first. The backup
  lives outside the skills folder so that Codex does not load the same skill twice.

  Usage:
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -WhatIf
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Force

  This file is ASCII only so that Windows PowerShell 5.1 reads it correctly without a BOM.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Force
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$source = $PSScriptRoot
if ($env:CODEX_HOME) { $codexHome = $env:CODEX_HOME } else { $codexHome = Join-Path $HOME '.codex' }
$skillsHome = Join-Path (Join-Path $HOME '.agents') 'skills'
$agentsHome = Join-Path $codexHome 'agents'
$backupRoot = Join-Path (Join-Path $HOME '.hirata-codex-backup') (Get-Date -Format 'yyyyMMdd-HHmmss')
$script:skipped = 0
$script:cmdlet = $PSCmdlet

function Get-SourceFiles([string]$root) {
    # Relative paths of the files to install, without Python caches.
    if (Test-Path -LiteralPath $root -PathType Leaf) { return @('') }
    $prefix = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\', '/')
    return @(Get-ChildItem -LiteralPath $root -Recurse -File |
        Where-Object { $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -ne '.pyc' } |
        ForEach-Object { $_.FullName.Substring($prefix.Length).TrimStart('\', '/') } |
        Sort-Object)
}

function Get-TreeHash([string]$root) {
    $lines = foreach ($rel in (Get-SourceFiles $root)) {
        if ($rel -eq '') { $file = $root } else { $file = Join-Path $root $rel }
        $rel.Replace('\', '/') + '|' + (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
    }
    return ($lines -join "`n")
}

function Copy-Tree([string]$from, [string]$to) {
    if (Test-Path -LiteralPath $from -PathType Leaf) {
        $parent = Split-Path -Parent $to
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $from -Destination $to -Force
        return
    }
    foreach ($rel in (Get-SourceFiles $from)) {
        $dest = Join-Path $to $rel
        $parent = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath (Join-Path $from $rel) -Destination $dest -Force
    }
}

function Install-One([string]$from, [string]$to) {
    if (-not (Test-Path -LiteralPath $to)) {
        if ($script:cmdlet.ShouldProcess($to, 'install')) {
            Copy-Tree $from $to
            Write-Host "installed  $to"
        }
        return
    }
    if ((Get-TreeHash $from) -eq (Get-TreeHash $to)) {
        Write-Host "unchanged  $to"
        return
    }
    if (-not $Force) {
        Write-Host "skipped    $to  (differs from this package; rerun with -Force to replace it, the old copy is backed up)"
        $script:skipped++
        return
    }
    if ($script:cmdlet.ShouldProcess($to, 'replace (old copy moved to backup)')) {
        if ($to.StartsWith($HOME, [StringComparison]::OrdinalIgnoreCase)) {
            $relative = $to.Substring($HOME.Length).TrimStart('\', '/')
        } else {
            $relative = ($to -replace '^[A-Za-z]:', '').TrimStart('\', '/')
        }
        $backup = Join-Path $backupRoot $relative
        $parent = Split-Path -Parent $backup
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Move-Item -LiteralPath $to -Destination $backup
        Copy-Tree $from $to
        Write-Host "replaced   $to  (backup: $backup)"
    }
}

Write-Host "Skills  -> $skillsHome"
foreach ($dir in (Get-ChildItem -LiteralPath (Join-Path $source 'skills') -Directory | Sort-Object Name)) {
    Install-One $dir.FullName (Join-Path $skillsHome $dir.Name)
}

Write-Host "Agents  -> $agentsHome"
foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $source 'agents') -Filter '*.toml' -File | Sort-Object Name)) {
    Install-One $file.FullName (Join-Path $agentsHome $file.Name)
}

Write-Host "AGENTS.md -> $codexHome"
Install-One (Join-Path $source 'AGENTS.md') (Join-Path $codexHome 'AGENTS.md')

# Files copied from a zip or a download carry the "from the internet" mark, which blocks scripts.
if (-not $WhatIfPreference -and [Environment]::OSVersion.Platform -eq 'Win32NT') {
    try {
        Get-ChildItem -LiteralPath $skillsHome -Recurse -File -ErrorAction SilentlyContinue | Unblock-File
    } catch {
        Write-Host "Unblock-File failed: $($_.Exception.Message)"
    }
}

# --- environment checks (report only) ---------------------------------------------------------

Write-Host ''
Write-Host "PowerShell $($PSVersionTable.PSVersion)"

$python = Get-Command python -ErrorAction SilentlyContinue
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($python -and $python.Source -notmatch 'WindowsApps') {
    Write-Host "python     $($python.Source)"
} elseif ($launcher) {
    Write-Host "python     not on PATH; use 'py -3' ($($launcher.Source))"
} else {
    Write-Host 'python     NOT FOUND. The skills run Python scripts; install Python 3.8 or later.'
}

if ($env:ADO_COLLECTION_URL) {
    Write-Host "ADO_COLLECTION_URL = $($env:ADO_COLLECTION_URL)"
} else {
    Write-Host 'ADO_COLLECTION_URL is not set. Set it once, then restart the terminal and Codex:'
    Write-Host "  [Environment]::SetEnvironmentVariable('ADO_COLLECTION_URL', 'https://<server>/tfs/<Collection>', 'User')"
}
if ($env:ADO_AUTH) { Write-Host "ADO_AUTH = $($env:ADO_AUTH)" } else { Write-Host 'ADO_AUTH is not set (Windows integrated auth is used).' }

Write-Host ''
if ($script:skipped -gt 0) {
    Write-Host "$($script:skipped) item(s) were skipped because they already exist with different content."
}
Write-Host 'Restart Codex to load the skills and agents.'
Write-Host 'For per-tool MCP approval, see config\mcp-approval.example.toml.'
