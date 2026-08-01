param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8092
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) {
    throw "Collector port $Port is already in use by process $($listener.OwningProcess)."
}

# The collector is deliberately isolated from the API/RAG process. It reads
# Supercell only, streams records to F: disk, and never initializes embeddings.
$env:RUNTIME_ROLE = "collector"
$env:RUNTIME_PORT = [string]$Port
$env:SUPERCELL_LIVE_DATA_ENABLED = "true"
$env:SUPERCELL_LEADERBOARD_PLAYERS = "20000"
$env:SUPERCELL_BATTLES_PER_PLAYER = "25"
$env:SUPERCELL_HIGH_VOLUME_REQUESTS_PER_SECOND = "1"
$env:SUPERCELL_HIGH_VOLUME_MAX_RETRIES = "0"
$env:SUPERCELL_HIGH_VOLUME_MAX_REFRESH_SECONDS = "28800"
$env:SNAPSHOT_PROGRESS_INTERVAL_SECONDS = "3600"

& (Join-Path $PSScriptRoot "run_backend.ps1")
exit $LASTEXITCODE
