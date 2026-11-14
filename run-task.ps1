param(
    [ValidateSet('economy','youth')][string]$Channel,
    [ValidateSet('auto','cards','approved','nextday','today','digest')][string]$Mode,
    [int]$RetryDelaySeconds = 120
)
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as cp949.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtime = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $runtime.python)) { throw 'Configured Python executable is missing' }
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) { throw 'Root .env is missing' }
if ($Channel -eq 'youth' -and -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'channels\youth\.env'))) { throw 'Youth .env is missing; no fallback to economy credentials' }
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:CARDNEWS_BROWSER_CHANNEL = $runtime.browser_channel

# Google Drive for desktop sometimes restarts itself and H: disappears for a few seconds,
# so file writes fail mid-run (2026-11-12 07:50, WinError 433). Card generation (auto/cards)
# is retried once after a pause. Publishing (approved) is NOT retried: re-running a run that
# was cut off mid-upload can post the same card twice; it runs every 10 minutes anyway.
$attempts = if ($Mode -eq 'approved' -or $Mode -eq 'digest') { 1 } else { 2 }  # digest: no double notice
$entry = Join-Path $PSScriptRoot 'task_entry.py'
for ($i = 1; $i -le $attempts; $i++) {
    & $runtime.python $entry --channel $Channel $Mode
    $code = $LASTEXITCODE
    if ($code -eq 0 -or $i -eq $attempts) { break }
    Start-Sleep -Seconds $RetryDelaySeconds
    for ($w = 0; $w -lt 30 -and -not (Test-Path -LiteralPath $entry); $w++) { Start-Sleep -Seconds 10 }  # wait up to 5 min for H:
    try {
        [IO.File]::AppendAllText((Join-Path $PSScriptRoot "logs\$Channel-$Mode.log"),
            "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [run-task] exit code $code, retrying once`r`n", (New-Object Text.UTF8Encoding $false))
    } catch {}
}
exit $code
