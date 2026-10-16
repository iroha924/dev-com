<#
.SYNOPSIS
  Thin REST client for Azure DevOps Server 2020 (REST api-version 6.0).

.DESCRIPTION
  Modes:
    get      GET a resource.
    query    POST to read-only endpoints only (_apis/wit/wiql, _apis/wit/workitemsbatch).
    preview  POST _apis/pipelines/{id}/runs only, and only with "previewRun": true in the body.
             The server expands the YAML and does not create a run.
    write    POST or PATCH anything. Run once with -DryRun to get the approval hash,
             show the target and the body to the user, then run again with -Approve <hash>.

  The response body is saved to -OutFile as received (UTF-8 bytes). One line of JSON that
  describes the result is printed to stdout. Diagnostics go to stderr.

  Environment:
    ADO_COLLECTION_URL  e.g. https://tfs.example.local/tfs/DefaultCollection (required)
    ADO_AUTH            default = Windows integrated auth (the default) | pat
    ADO_PAT             personal access token when ADO_AUTH=pat. It is never printed.

  Exit codes:
    0 success, 1 HTTP error or no response (read modes), 2 usage error or refused,
    3 write sent but the result is unknown. Do not resend; read the target back first.

  This file is ASCII only so that Windows PowerShell 5.1 reads it correctly without a BOM.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('get', 'query', 'preview', 'write')]
    [string]$Mode,

    [Parameter(Mandatory = $true, Position = 1)]
    [string]$Path,

    [ValidateSet('POST', 'PATCH')]
    [string]$Method,

    [string]$BodyFile,
    [string]$ContentType,
    [string]$ApiVersion,
    [string]$OutFile,
    [string]$ContinuationToken,
    [switch]$DryRun,
    [string]$Approve,

    [ValidateSet('application/json', 'text/plain')]
    [string]$Accept = 'application/json',

    [int]$TimeoutSec = 100
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Write-Result($result) {
    [Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))
}

function Stop-Script([int]$code, [string]$message) {
    [Console]::Error.WriteLine("ado.ps1: $message")
    exit $code
}

function Resolve-LocalPath([string]$p) {
    # [IO.File] resolves relative paths against the process directory, not the PowerShell location.
    return $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($p)
}

function Get-Sha256Hex([byte[]]$bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

# --- target URL -----------------------------------------------------------------------------

$base = $env:ADO_COLLECTION_URL
if (-not $base) { Stop-Script 2 'ADO_COLLECTION_URL is not set.' }
$base = $base.TrimEnd('/')

# Credentials are sent with the request, so the target must stay under the collection.
# Dot segments are refused outright: System.Uri and the server may resolve them differently.
if ($Path -match '(^|[/\\])(\.|%2e){1,2}([/\\?#]|$)') {
    Stop-Script 2 "Dot segments (. or ..) in the path are refused: $Path"
}
if ($Path -match '^[a-zA-Z][a-zA-Z0-9+.-]*://') {
    $url = $Path
} else {
    $url = $base + '/' + $Path.TrimStart('/')
}
$baseUri = [Uri]($base + '/')
$targetUri = [Uri]$url
if ($targetUri.Scheme -ne $baseUri.Scheme -or $targetUri.Host -ne $baseUri.Host -or $targetUri.Port -ne $baseUri.Port -or
    -not $targetUri.AbsolutePath.StartsWith($baseUri.AbsolutePath, [StringComparison]::OrdinalIgnoreCase)) {
    Stop-Script 2 "URL outside ADO_COLLECTION_URL is refused: $url"
}

if (-not $ApiVersion) {
    if ($Mode -eq 'preview') { $ApiVersion = '6.0-preview.1' } else { $ApiVersion = '6.0' }
}
if ($url -notmatch '[?&]api-version=') {
    $sep = '?'
    if ($url.Contains('?')) { $sep = '&' }
    $url = $url + $sep + 'api-version=' + $ApiVersion
}
if ($ContinuationToken) {
    $url = $url + '&continuationToken=' + [Uri]::EscapeDataString($ContinuationToken)
}

$pathOnly = ([Uri]$url).AbsolutePath

# --- mode rules -----------------------------------------------------------------------------

if ($Mode -ne 'write' -and ($DryRun -or $Approve)) { Stop-Script 2 '-DryRun and -Approve are for write mode only.' }

switch ($Mode) {
    'get' {
        if ($Method -or $BodyFile) { Stop-Script 2 'get takes no -Method or -BodyFile.' }
        $httpMethod = 'GET'
    }
    'query' {
        if ($Method) { Stop-Script 2 'query takes no -Method.' }
        if ($pathOnly -notmatch '/_apis/wit/(wiql|workitemsbatch)/?$') {
            Stop-Script 2 "query accepts only _apis/wit/wiql and _apis/wit/workitemsbatch: $pathOnly"
        }
        if (-not $BodyFile) { Stop-Script 2 'query needs -BodyFile.' }
        $httpMethod = 'POST'
    }
    'preview' {
        if ($Method) { Stop-Script 2 'preview takes no -Method.' }
        if ($pathOnly -notmatch '/_apis/pipelines/\d+/runs/?$') {
            Stop-Script 2 "preview accepts only _apis/pipelines/{id}/runs: $pathOnly"
        }
        if (-not $BodyFile) { Stop-Script 2 'preview needs -BodyFile.' }
        $httpMethod = 'POST'
    }
    'write' {
        if (-not $Method) { Stop-Script 2 'write needs -Method POST or PATCH.' }
        if (-not $BodyFile) { Stop-Script 2 'write needs -BodyFile.' }
        if (-not $DryRun -and -not $Approve) { Stop-Script 2 'write needs -DryRun first, then -Approve <hash>.' }
        $httpMethod = $Method
    }
}

# --- body -----------------------------------------------------------------------------------

$bodyBytes = $null
if ($BodyFile) {
    $BodyFile = Resolve-LocalPath $BodyFile
    if (-not (Test-Path -LiteralPath $BodyFile -PathType Leaf)) { Stop-Script 2 "Body file not found: $BodyFile" }
    $bodyBytes = [IO.File]::ReadAllBytes($BodyFile)
    if ($bodyBytes.Length -ge 3 -and $bodyBytes[0] -eq 0xEF -and $bodyBytes[1] -eq 0xBB -and $bodyBytes[2] -eq 0xBF) {
        Stop-Script 2 'Body file starts with a UTF-8 BOM. Write it without a BOM (for example with Python).'
    }
    try {
        $bodyText = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bodyBytes)
        $bodyObject = $bodyText | ConvertFrom-Json
    } catch {
        Stop-Script 2 "Body file is not valid UTF-8 JSON: $BodyFile"
    }
    if ($Mode -eq 'preview') {
        $names = @($bodyObject.PSObject.Properties | ForEach-Object { $_.Name })
        if (-not ($names -contains 'previewRun') -or -not ($bodyObject.previewRun -is [bool]) -or -not $bodyObject.previewRun) {
            Stop-Script 2 'preview body must contain "previewRun": true (a JSON boolean).'
        }
    }
}

if (-not $ContentType) {
    # Only creating or updating the work item itself takes JSON Patch; comments and other sub-resources take JSON.
    if ($pathOnly -match '/_apis/wit/workitems/(\$[^/]+|\d+)/?$' -and ($httpMethod -eq 'PATCH' -or $httpMethod -eq 'POST')) {
        $ContentType = 'application/json-patch+json'
    } else {
        $ContentType = 'application/json'
    }
}

# --- approval for writes --------------------------------------------------------------------

if ($Mode -eq 'write') {
    $header = [Text.Encoding]::UTF8.GetBytes($httpMethod + "`n" + $url + "`n" + $ContentType + "`n")
    $all = New-Object byte[] ($header.Length + $bodyBytes.Length)
    [Array]::Copy($header, 0, $all, 0, $header.Length)
    [Array]::Copy($bodyBytes, 0, $all, $header.Length, $bodyBytes.Length)
    $hash = Get-Sha256Hex $all
    if ($DryRun) {
        Write-Result ([ordered]@{
            dryRun      = $true
            method      = $httpMethod
            url         = $url
            contentType = $ContentType
            bodyFile    = $BodyFile
            bodyBytes   = $bodyBytes.Length
            approval    = $hash
        })
        exit 0
    }
    if ($Approve.ToLowerInvariant() -ne $hash) {
        Stop-Script 2 'Approval hash does not match this method, URL, content type and body. Run -DryRun again and get approval again.'
    }
}

# --- auth -----------------------------------------------------------------------------------

Add-Type -AssemblyName System.Net.Http
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    # Not supported on every runtime; HttpClient on PowerShell 7 negotiates TLS by itself.
}

$handler = New-Object System.Net.Http.HttpClientHandler
# Do not follow redirects: integrated auth would answer a challenge from wherever we are sent.
$handler.AllowAutoRedirect = $false

$authHeader = $null
$authMode = $env:ADO_AUTH
if (-not $authMode) { $authMode = 'default' }
switch ($authMode) {
    'default' { $handler.UseDefaultCredentials = $true }
    'pat' {
        if (-not $env:ADO_PAT) { Stop-Script 2 'ADO_AUTH=pat but ADO_PAT is not set.' }
        if (-not $url.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
            Stop-Script 2 'A PAT is not sent over plain http. Use https or ADO_AUTH=default.'
        }
        $authHeader = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(':' + $env:ADO_PAT))
    }
    default { Stop-Script 2 "Unknown ADO_AUTH: $authMode (use default or pat)" }
}

# --- output file (checked before sending, so a write never fails after it was applied) -------

if (-not $OutFile) {
    $stamp = [DateTime]::Now.ToString('yyyyMMdd-HHmmss')
    $suffix = [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $extension = '.json'
    if ($Accept -eq 'text/plain') { $extension = '.txt' }
    $OutFile = Join-Path ([IO.Path]::GetTempPath()) ('ado-' + $stamp + '-' + $suffix + $extension)
} else {
    $OutFile = Resolve-LocalPath $OutFile
}
$outDir = Split-Path -Parent $OutFile
if (-not (Test-Path -LiteralPath $outDir -PathType Container)) { Stop-Script 2 "Folder for -OutFile does not exist: $outDir" }
if (Test-Path -LiteralPath $OutFile -PathType Container) { Stop-Script 2 "-OutFile is a folder: $OutFile" }

# --- send -----------------------------------------------------------------------------------

$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
$request = New-Object System.Net.Http.HttpRequestMessage((New-Object System.Net.Http.HttpMethod($httpMethod)), $url)
$request.Headers.Accept.ParseAdd($Accept)
if ($authHeader) {
    $request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Basic', $authHeader)
}
if ($null -ne $bodyBytes) {
    $content = New-Object System.Net.Http.ByteArrayContent(, $bodyBytes)
    $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse($ContentType + '; charset=utf-8')
    $request.Content = $content
}

try {
    $response = $client.SendAsync($request).GetAwaiter().GetResult()
} catch {
    $reason = $_.Exception.Message
    if ($Mode -eq 'write') {
        Write-Result ([ordered]@{ status = 'unknown'; ok = $false; method = $httpMethod; url = $url })
        Stop-Script 3 "No response ($reason). The write may have been applied. Read the target back before doing anything else."
    }
    Write-Result ([ordered]@{ status = 'no-response'; ok = $false; method = $httpMethod; url = $url })
    Stop-Script 1 "No response: $reason"
}

$responseBytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
try {
    [IO.File]::WriteAllBytes($OutFile, $responseBytes)
} catch {
    $reason = $_.Exception.Message
    Write-Result ([ordered]@{ status = [int]$response.StatusCode; ok = $response.IsSuccessStatusCode; method = $httpMethod; url = $url; out = $null })
    if ($Mode -eq 'write') {
        Stop-Script 3 "The server answered HTTP $([int]$response.StatusCode) but the response could not be saved ($reason). Do not resend; read the target back."
    }
    Stop-Script 1 "The response could not be saved: $reason"
}

$token = $null
$values = $null
if ($response.Headers.TryGetValues('x-ms-continuationtoken', [ref]$values)) {
    $token = @($values)[0]
}
$location = $null
if ($null -ne $response.Headers.Location) { $location = $response.Headers.Location.ToString() }

$ok = $response.IsSuccessStatusCode
Write-Result ([ordered]@{
    status            = [int]$response.StatusCode
    ok                = $ok
    method            = $httpMethod
    url               = $url
    out               = $OutFile
    bytes             = $responseBytes.Length
    continuationToken = $token
    location          = $location
})
if (-not $ok) {
    [Console]::Error.WriteLine("ado.ps1: HTTP $([int]$response.StatusCode). See $OutFile")
    exit 1
}
exit 0
