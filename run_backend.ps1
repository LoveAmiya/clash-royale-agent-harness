$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Prefer the project virtual environment over the system Python.
$localPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $pythonExe = $localPython
} else {
    $pythonExe = "python"
}

if (-not $env:RUNTIME_PORT) {
    $env:RUNTIME_PORT = "8091"
}

Write-Host "Starting Clash Royale Agent backend..."
Write-Host "Health: http://127.0.0.1:$env:RUNTIME_PORT/health"
Write-Host "Python: $pythonExe"

& $pythonExe runtime_multi.py
