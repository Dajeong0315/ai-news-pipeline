param([switch]$Install, [switch]$InstallNode, [string]$PythonExecutable = 'py', [string]$RuntimeDirectory = '', [string]$BrowserChannel = 'msedge')
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not $Install) { Write-Output 'Use -Install to create venv and install dependencies. No schedule or posting is enabled.'; exit 0 }
if (-not $RuntimeDirectory) { $RuntimeDirectory = Join-Path $PSScriptRoot 'venv' }
if ($PythonExecutable -eq 'py') { & py -3.12 -m venv $RuntimeDirectory } else { & $PythonExecutable -m venv $RuntimeDirectory }
if ($LASTEXITCODE) { throw 'Python venv creation failed' }
$python = Join-Path $RuntimeDirectory 'Scripts\python.exe'
& $python -m pip install -r requirements-recovery.lock.txt
if ($LASTEXITCODE) { throw 'pip installation failed' }
if ($InstallNode) {
    Push-Location (Join-Path $PSScriptRoot 'environment')
    try { npm.cmd ci; if ($LASTEXITCODE) { throw 'npm ci failed' }; if (-not $BrowserChannel) { npx.cmd playwright install chromium; if ($LASTEXITCODE) { throw 'Chromium installation failed' } } } finally { Pop-Location }
}
@{python=$python;browser_channel=$BrowserChannel;candidate=$PSScriptRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime.local.json') -Encoding utf8
Write-Output 'Configure root .env and channels/youth/.env separately; run doctor.py --online for each channel. No schedules were enabled.'
