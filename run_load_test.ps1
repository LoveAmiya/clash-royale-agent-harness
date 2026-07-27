param(
    [ValidateSet("smoke", "load", "soak")]
    [string]$Profile = "smoke",
    [string]$SoakDuration = "30m"
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "compose.loadtest.yml"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$summaryPath = "/reports/$Profile-$timestamp.json"

docker compose -f $composeFile up --build -d redis api-1 api-2 proxy
if ($LASTEXITCODE -ne 0) { throw "Load-test stack failed to start" }

docker compose -f $composeFile --profile loadtest run --rm `
    -e "SOAK_DURATION=$SoakDuration" `
    -e "SUMMARY_PATH=$summaryPath" `
    k6 run "/scripts/$Profile.js"
if ($LASTEXITCODE -ne 0) {
    throw "k6 $Profile thresholds failed; report retained at load/reports/$Profile-$timestamp.json"
}

Write-Host "k6 $Profile passed; report: load/reports/$Profile-$timestamp.json"

