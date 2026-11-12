param([ValidateSet('DRY_RUN','LIVE')][string]$ExecutionMode = 'DRY_RUN')
$ErrorActionPreference = 'Stop'
$PublisherRoot = Split-Path $PSScriptRoot -Parent
Set-Location $PublisherRoot
# Task Scheduler may inherit an old environment; load the user's configured bindings explicitly.
foreach ($PublisherName in @('PUBLISHER_R2_ACCESS_KEY_ID','PUBLISHER_R2_SECRET_ACCESS_KEY',
    'PUBLISHER_R2_ENDPOINT_URL','PUBLISHER_R2_BUCKET','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY',
    'R2_ENDPOINT','R2_ACCOUNT_ID','R2_BUCKET_NAME')) {
    $PublisherValue = [Environment]::GetEnvironmentVariable($PublisherName, 'User')
    if ($PublisherValue) { [Environment]::SetEnvironmentVariable($PublisherName, $PublisherValue, 'Process') }
}
$env:PYTHONUTF8 = '1'
# LIVE in this process comes only from the owner's explicit task installation mode.
$env:PUBLISHER_ALLOW_LIVE = if ($ExecutionMode -eq 'LIVE') { 'true' } else { 'false' }
$PublisherPython = Join-Path $PublisherRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PublisherPython)) { throw 'Install project dependencies before scheduling.' }
$PublisherLogDir = Join-Path $PublisherRoot 'data\schedule-logs'
New-Item -ItemType Directory -Path $PublisherLogDir -Force | Out-Null
$PublisherLog = Join-Path $PublisherLogDir ((Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$ErrorActionPreference = 'Continue' # Native stderr is logged, not treated as a PowerShell terminating error.
if ($ExecutionMode -eq 'LIVE') {
    & $PublisherPython -m android_publisher daily --lookback 1 --live *> $PublisherLog
} else {
    & $PublisherPython -m android_publisher daily --lookback 1 *> $PublisherLog
}
exit $LASTEXITCODE
