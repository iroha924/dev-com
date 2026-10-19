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

function Get-FileSha256([string]$path) {
    # .NET rather than Get-FileHash: under -WhatIf, Windows PowerShell 5.1's Get-FileHash only reports what it
    # would do and returns nothing, even though reading a file changes nothing.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try {
        return [System.BitConverter]::ToString($sha.ComputeHash($stream))
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Get-TreeHash([string]$root) {
    $lines = foreach ($rel in (Get-SourceFiles $root)) {
        if ($rel -eq '') { $file = $root } else { $file = Join-Path $root $rel }
        $rel.Replace('\', '/') + '|' + (Get-FileSha256 $file)
    }
    return ($lines -join "`n")
}

function Copy-Tree([string]$from, [string]$to) {
    # Called only after Install-One confirmed the whole item, so -Confirm does not ask again for each file;
    # a "no" there would leave a partial copy that is still reported as installed or replaced.
    if (Test-Path -LiteralPath $from -PathType Leaf) {
        $parent = Split-Path -Parent $to
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force -Confirm:$false | Out-Null }
        Copy-Item -LiteralPath $from -Destination $to -Force -Confirm:$false
        return
    }
    foreach ($rel in (Get-SourceFiles $from)) {
        $dest = Join-Path $to $rel
        $parent = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force -Confirm:$false | Out-Null }
        Copy-Item -LiteralPath (Join-Path $from $rel) -Destination $dest -Force -Confirm:$false
    }
}

function Install-One([string]$from, [string]$to) {
    # Returns what happened: installed, unchanged, replaced, skipped (differs, kept) or whatif (not done).
    if (-not (Test-Path -LiteralPath $to)) {
        if (-not $script:cmdlet.ShouldProcess($to, 'install')) { return 'whatif' }
        Copy-Tree $from $to
        Write-Host "installed  $to"
        return 'installed'
    }
    if ((Get-TreeHash $from) -eq (Get-TreeHash $to)) {
        Write-Host "unchanged  $to"
        return 'unchanged'
    }
    if (-not $Force) {
        Write-Host "skipped    $to  (differs from this package; rerun with -Force to replace it, the old copy is backed up)"
        $script:skipped++
        return 'skipped'
    }
    if (-not $script:cmdlet.ShouldProcess($to, 'replace (old copy moved to backup)')) { return 'whatif' }
    if ($to.StartsWith($HOME, [StringComparison]::OrdinalIgnoreCase)) {
        $relative = $to.Substring($HOME.Length).TrimStart('\', '/')
    } else {
        $relative = ($to -replace '^[A-Za-z]:', '').TrimStart('\', '/')
    }
    $backup = Join-Path $backupRoot $relative
    $parent = Split-Path -Parent $backup
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force -Confirm:$false | Out-Null }
    Move-Item -LiteralPath $to -Destination $backup -Confirm:$false
    Copy-Tree $from $to
    Write-Host "replaced   $to  (backup: $backup)"
    return 'replaced'
}

Write-Host "Skills  -> $skillsHome"
$skillResults = @{}
foreach ($dir in (Get-ChildItem -LiteralPath (Join-Path $source 'skills') -Directory | Sort-Object Name)) {
    $skillResults[$dir.Name] = Install-One $dir.FullName (Join-Path $skillsHome $dir.Name)
}

Write-Host "Agents  -> $agentsHome"
foreach ($file in (Get-ChildItem -LiteralPath (Join-Path $source 'agents') -Filter '*.toml' -File | Sort-Object Name)) {
    Install-One $file.FullName (Join-Path $agentsHome $file.Name) | Out-Null
}

Write-Host "AGENTS.md -> $codexHome"
Install-One (Join-Path $source 'AGENTS.md') (Join-Path $codexHome 'AGENTS.md') | Out-Null

$hookScript = Join-Path (Join-Path (Join-Path $codexHome 'hooks') 'hirata-codex') 'codex_status.py'
Write-Host "Hook    -> $hookScript"
Install-One (Join-Path (Join-Path $source 'hooks') 'codex_status.py') $hookScript | Out-Null

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

function Get-JsonProperty($object, [string]$name) {
    # The value of a property parsed by ConvertFrom-Json, or $null when there is no such object or property.
    # StrictMode makes reading a missing property an error, so check first.
    if ($object -isnot [System.Management.Automation.PSCustomObject]) { return $null }
    $property = $object.PSObject.Properties[$name]
    # The leading comma keeps an array as one value; without it ["x"] would come back as the string "x".
    if ($property) { return , $property.Value }
    return $null
}

function Find-MarkedHandlers($config, [string]$marker) {
    # Every handler under hooks.UserPromptSubmit[].hooks[] whose statusMessage is the marker, before checking its shape,
    # so that a well-formed handler next to a broken one is seen as a duplicate.
    $found = @()
    $groups = Get-JsonProperty (Get-JsonProperty $config 'hooks') 'UserPromptSubmit'
    foreach ($group in @(if ($null -ne $groups) { $groups })) {
        $handlers = Get-JsonProperty $group 'hooks'
        foreach ($handler in @(if ($null -ne $handlers) { $handlers })) {
            $status = Get-JsonProperty $handler 'statusMessage'
            if ($status -is [string] -and [string]::Equals($status, $marker, [StringComparison]::Ordinal)) { $found += , $handler }
        }
    }
    return , $found
}

$hooksJson = Join-Path $codexHome 'hooks.json'
$script:hookReady = $false
if (-not $hookCommand) {
    Write-Host "skipped    $hooksJson  (Python was not found; install Python and rerun)"
} elseif (Test-Path -LiteralPath $hooksJson) {
    # This file is written as UTF-8 without a BOM, which Windows PowerShell 5.1 would otherwise read as ANSI.
    # Get-Content -Raw returns nothing for an empty file.
    $existing = Get-Content -LiteralPath $hooksJson -Raw -Encoding UTF8
    $config = $null
    $readable = $false
    if ($existing) {
        try {
            $config = ConvertFrom-Json -InputObject $existing
            $readable = $true
        } catch {
            $readable = $false
        }
    }
    $handlerLine = '  {"hooks": [{"type": "command", "command": ' + (ConvertTo-Json $hookCommand) + ', "timeout": 10, "statusMessage": "' + $hookMarker + '"}]}'
    $found = @()
    if ($readable) { $found = Find-MarkedHandlers $config $hookMarker }
    if (-not $readable) {
        Write-Host "skipped    $hooksJson  (not readable as JSON; fix it, then add this handler under hooks.UserPromptSubmit):"
        Write-Host $handlerLine
        $script:skipped++
    } elseif ($found.Count -eq 0) {
        Write-Host "skipped    $hooksJson  (exists; add this handler under hooks.UserPromptSubmit yourself):"
        Write-Host $handlerLine
        $script:skipped++
    } elseif ($found.Count -gt 1) {
        Write-Host "skipped    $hooksJson  ($($found.Count) handlers carry the statusMessage `"$hookMarker`"; keep only this one):"
        Write-Host $handlerLine
        $script:skipped++
    } else {
        $handler = $found[0]
        $type = Get-JsonProperty $handler 'type'
        $command = Get-JsonProperty $handler 'command'
        $plain = ($type -is [string]) -and [string]::Equals($type, 'command', [StringComparison]::Ordinal) -and
            ($command -is [string]) -and -not $handler.PSObject.Properties['commandWindows']
        if (-not $plain) {
            # commandWindows overrides command on Windows; this installer never writes it, so a person added it.
            Write-Host "skipped    $hooksJson  (the hirata-codex handler is not a plain command handler; check its type, command and commandWindows, then make it):"
            Write-Host $handlerLine
            $script:skipped++
        } elseif ([string]::Equals($command, $hookCommand, [StringComparison]::Ordinal)) {
            Write-Host "unchanged  $hooksJson  (the hirata-codex hook definition matches; check in /hooks that it is enabled and trusted)"
            $script:hookReady = $true
        } else {
            Write-Host "skipped    $hooksJson  (the hirata-codex hook runs another command; replace its command, then trust it again in /hooks):"
            Write-Host "  now: $command"
            Write-Host "  new: $hookCommand"
            Write-Host ('  as JSON: "command": ' + (ConvertTo-Json $hookCommand))
            $script:skipped++
        }
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
# The mark is removed only from this package's files in the skills that now match it (installed, unchanged
# or replaced). A skill kept because it differs, files someone added next to ours (such as .pyc), and other
# skills under the same folder keep their mark.
if (-not $WhatIfPreference -and [Environment]::OSVersion.Platform -eq 'Win32NT') {
    foreach ($dir in (Get-ChildItem -LiteralPath (Join-Path $source 'skills') -Directory)) {
        if (@('installed', 'unchanged', 'replaced') -notcontains $skillResults[$dir.Name]) { continue }
        $target = Join-Path $skillsHome $dir.Name
        foreach ($rel in (Get-SourceFiles $dir.FullName)) {
            try {
                Unblock-File -LiteralPath (Join-Path $target $rel)
            } catch {
                Write-Host "Unblock-File failed for $(Join-Path $target $rel): $($_.Exception.Message)"
            }
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
