$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (-not $env:WEB_PORT) {
    $env:WEB_PORT = "8080"
}

if (-not $env:BACKEND_URL) {
    $env:BACKEND_URL = "http://127.0.0.1:8091/process"
}

Write-Host "Starting Clash Royale Agent web UI..."
Write-Host "Open http://127.0.0.1:$env:WEB_PORT"
Write-Host "Backend URL: $env:BACKEND_URL"

python web_app.py
