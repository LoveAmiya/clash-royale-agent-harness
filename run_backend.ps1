$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not $env:RUNTIME_PORT) {
    $env:RUNTIME_PORT = "8091"
}

Write-Host "Starting Clash Royale Agent backend..."
Write-Host "Health: http://127.0.0.1:$env:RUNTIME_PORT/health"

python runtime_multi.py
