param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8091
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    throw "API port $Port is already in use by process $($listener.OwningProcess)."
}

# API workers follow atomically published snapshots and never contact
# Supercell. This keeps long collection isolated from user-facing requests.
$env:RUNTIME_ROLE = "api"
$env:RUNTIME_PORT = [string]$Port
$env:SUPERCELL_LIVE_DATA_ENABLED = "true"
$env:SNAPSHOT_AUTO_FOLLOW_ENABLED = "false"

& (Join-Path $PSScriptRoot "run_backend.ps1")
exit $LASTEXITCODE
