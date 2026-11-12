param(
    [ValidateSet('setup', 'doctor', 'dry-run')]
    [string]$Command = 'setup'
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$PublisherPython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path $PublisherPython)) {
    Write-Host 'Creating Python 3.11+ virtual environment...'
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ and try again.' }
}
& $PublisherPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed. Check network and Python version.' }
if ($Command -eq 'doctor') {
    & $PublisherPython -m android_publisher doctor --job data/first-job.json --download
} elseif ($Command -eq 'dry-run') {
    & $PublisherPython -m android_publisher dry-run --job data/first-job.json --interactive
} else {
    & $PublisherPython -m android_publisher setup
}
exit $LASTEXITCODE
