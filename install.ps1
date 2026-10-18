<#
.SYNOPSIS
  Install the dev-com skills, custom agents and global AGENTS.md for Codex on Windows.

.DESCRIPTION
  Copies
    skills\<name>\       -> $HOME\.agents\skills\<name>\
    agents\<name>.toml   -> <CODEX_HOME>\agents\<name>.toml   (CODEX_HOME defaults to $HOME\.codex)
    AGENTS.md            -> <CODEX_HOME>\AGENTS.md
    hooks\codex_status.py -> <CODEX_HOME>\hooks\hirata-codex\codex_status.py
  and writes <CODEX_HOME>\hooks.json for that hook when no hooks.json exists yet. An existing
  hooks.json is never changed, not even with -Force, because it may hold other hooks; the lines
  to add are printed instead. Codex runs the hook only after you trust it in /hooks.

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

$hookScript = Join-Path (Join-Path (Join-Path $codexHome 'hooks') 'hirata-codex') 'codex_status.py'
Write-Host "Hook    -> $hookScript"
Install-One (Join-Path (Join-Path $source 'hooks') 'codex_status.py') $hookScript

# The hook needs a real Python; the Microsoft Store stub under WindowsApps only opens the Store.
$python = Get-Command python -ErrorAction SilentlyContinue
$launcher = Get-Command py -ErrorAction SilentlyContinue
$pythonCall = $null
if ($python -and $python.Source -notmatch 'WindowsApps') {
    $pythonCall = @($python.Source)
} elseif ($launcher) {
    $pythonCall = @($launcher.Source, '-3')
}
. (Join-Path (Join-Path $source 'hooks') 'New-HookCommand.ps1')
$hookCommand = $null
if ($pythonCall) {
    $onWindows = [Environment]::OSVersion.Platform -eq 'Win32NT'
    $systemPowerShell = $null
    if ($onWindows) {
        $systemPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    }
    $hookCommand = New-HookCommand -PythonCall $pythonCall -Script $hookScript -Windows:$onWindows -PowerShellPath $systemPowerShell
}
# The command may be encoded, so the handler carries a plain marker to recognize it on later runs.
$hookMarker = 'hirata-codex usage status'

$hooksJson = Join-Path $codexHome 'hooks.json'
$script:hookReady = $false
if (-not $hookCommand) {
    Write-Host "skipped    $hooksJson  (Python was not found; install Python and rerun)"
} elseif (Test-Path -LiteralPath $hooksJson) {
    # Get-Content -Raw returns nothing for an empty file, so test before calling a method on it.
    $existing = Get-Content -LiteralPath $hooksJson -Raw
    if ($existing -and $existing.Contains($hookMarker)) {
        Write-Host "unchanged  $hooksJson  (already has the hirata-codex hook)"
        $script:hookReady = $true
    } else {
        Write-Host "skipped    $hooksJson  (exists; add this handler under hooks.UserPromptSubmit yourself):"
        Write-Host ('  {"hooks": [{"type": "command", "command": ' + (ConvertTo-Json $hookCommand) + ', "timeout": 10, "statusMessage": "' + $hookMarker + '"}]}')
        $script:skipped++
    }
} elseif ($script:cmdlet.ShouldProcess($hooksJson, 'write')) {
    $handler = [ordered]@{ type = 'command'; command = $hookCommand; timeout = 10; statusMessage = $hookMarker }
    $config = [ordered]@{ hooks = [ordered]@{ UserPromptSubmit = @([ordered]@{ hooks = @($handler) }) } }
    $text = ConvertTo-Json -InputObject $config -Depth 6
    # UTF-8 without a BOM: Windows PowerShell 5.1 Set-Content would add one.
    [IO.File]::WriteAllText($hooksJson, $text, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "installed  $hooksJson  (open /hooks in Codex and trust the hook once)"
    $script:hookReady = $true
}

# Files copied from a zip or a download carry the "from the internet" mark, which blocks scripts.
# Only this package's skill folders are touched; other skills under the same folder keep their mark.
if (-not $WhatIfPreference -and [Environment]::OSVersion.Platform -eq 'Win32NT') {
    foreach ($dir in (Get-ChildItem -LiteralPath (Join-Path $source 'skills') -Directory)) {
        $target = Join-Path $skillsHome $dir.Name
        try {
            Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction SilentlyContinue | Unblock-File
        } catch {
            Write-Host "Unblock-File failed for ${target}: $($_.Exception.Message)"
        }
    }
}

# --- environment checks (report only) ---------------------------------------------------------

Write-Host ''
Write-Host "PowerShell $($PSVersionTable.PSVersion)"

if ($python -and $python.Source -notmatch 'WindowsApps') {
    Write-Host "python     $($python.Source)"
} elseif ($launcher) {
    Write-Host "python     not on PATH; use 'py -3' ($($launcher.Source))"
} else {
    Write-Host 'python     NOT FOUND. The skills run Python scripts; install Python 3.11.'
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
if ($script:hookReady) {
    Write-Host 'Then open /hooks in Codex and trust the "hirata-codex usage status" hook (once; again only if hooks.json changes).'
} else {
    Write-Host 'The usage status hook is not active yet; see the hooks.json line above.'
}
Write-Host 'For per-tool MCP approval, see config\mcp-approval.example.toml.'
