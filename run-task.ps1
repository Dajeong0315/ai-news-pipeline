param(
    [ValidateSet('economy','youth')][string]$Channel,
    [ValidateSet('auto','cards','approved')][string]$Mode
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.local.json') -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $runtime.python)) { throw 'Configured Python executable is missing' }
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) { throw 'Root .env is missing' }
if ($Channel -eq 'youth' -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'channels\youth\.env'))) { throw 'Youth .env is missing; no fallback to economy credentials' }
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:CARDNEWS_BROWSER_CHANNEL = $runtime.browser_channel
& $runtime.python (Join-Path $PSScriptRoot 'task_entry.py') --channel $Channel $Mode
exit $LASTEXITCODE
