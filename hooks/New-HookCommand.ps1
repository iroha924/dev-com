<#
  Builds the "command" string of the hirata-codex hook for hooks.json. install.ps1 dot-sources this file,
  and tests/test_hook_command.py runs it directly.

  Codex runs a hook through the session's shell: "pwsh/powershell -NoProfile -Command <command>", or
  "cmd.exe /C <command>" when there is no session shell. The command must therefore mean the same thing
  in both shells:
    - Paths made only of plain characters are written as they are, executable first. No PowerShell is
      started, so nothing looks like an obfuscated PowerShell call to endpoint security tools.
    - Otherwise powershell.exe is started by its full path (cmd looks for a bare name in the current
      directory first, which would run an exe planted in the repository) with an encoded command. The
      paths travel inside it as base64 of UTF-8, so spaces, Japanese and any kind of quote survive.
      The encoded command always exits 0 so that a broken hook never blocks the prompt.

  This file is ASCII only so that Windows PowerShell 5.1 reads it correctly without a BOM.
#>

$script:PlainPathPattern = '^[A-Za-z/][A-Za-z0-9_.:\\/~-]*$'

function New-HookCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$PythonCall,   # python.exe, or py.exe followed by -3
        [Parameter(Mandatory = $true)][string]$Script,
        [switch]$Windows,
        [string]$PowerShellPath
    )
    $parts = @($PythonCall) + @($Script)
    if (-not $Windows) {
        return (($parts | ForEach-Object { if ($_ -like '-*') { $_ } else { '"' + $_ + '"' } }) -join ' ')
    }
    $unsafe = @($parts | Where-Object { $_ -notlike '-*' -and $_ -notmatch $script:PlainPathPattern })
    if ($unsafe.Count -eq 0) {
        return ($parts -join ' ')
    }
    if (-not $PowerShellPath -or $PowerShellPath -notmatch $script:PlainPathPattern) {
        throw "PowerShell path must be a plain full path: $PowerShellPath"
    }
    $decoded = foreach ($part in $parts) {
        if ($part -like '-*') {
            "'" + $part + "'"
        } else {
            $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($part))
            "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + $b64 + "')))"
        }
    }
    $inner = '$a = @(' + ($decoded -join ', ') + '); $exe = $a[0]; $rest = @($a | Select-Object -Skip 1); ' +
        'try { & $exe @rest } catch { }; exit 0'
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($inner))
    return $PowerShellPath + ' -NoProfile -NonInteractive -EncodedCommand ' + $encoded
}
