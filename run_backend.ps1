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

# Keep this project's runtime aligned with the Codex OpenAI-compatible relay.
# API credentials are still read only from OPENAI_API_KEY in the environment.
$env:OPENAI_BASE_URL = "https://crs.ruinique.com"
$env:OPENAI_WIRE_API = "responses"
if (-not $env:OPENAI_MODEL) {
    $env:OPENAI_MODEL = "gpt-5.5"
}
$env:OPENAI_REASONING_EFFORT = "medium"
$env:PARSER_REASONING_EFFORT = "medium"
$env:SYNTHESIS_REASONING_EFFORT = "medium"

Write-Host "Starting Clash Royale Agent backend..."
Write-Host "Health: http://127.0.0.1:$env:RUNTIME_PORT/health"
Write-Host "Python: $pythonExe"
Write-Host "Model: $env:OPENAI_MODEL via $env:OPENAI_WIRE_API"

& $pythonExe runtime_multi.py
