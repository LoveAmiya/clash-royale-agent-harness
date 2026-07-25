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
if (-not $env:EXTERNAL_API_REQUIRED) {
    $env:EXTERNAL_API_REQUIRED = "true"
}
$env:SUPERCELL_CACHE_TTL_SECONDS = "86400"

# A Codex-launched shell may not inherit variables added to the Windows user
# environment after the desktop app started. Import the persisted API token
# only when the current process does not already provide one; never print it.
if ([string]::IsNullOrWhiteSpace($env:SUPERCELL_API_TOKEN)) {
    $persistedSupercellToken = [Environment]::GetEnvironmentVariable("SUPERCELL_API_TOKEN", "User")
    if (-not [string]::IsNullOrWhiteSpace($persistedSupercellToken)) {
        $env:SUPERCELL_API_TOKEN = $persistedSupercellToken
    }
}

$supercellTokenConfigured = -not [string]::IsNullOrWhiteSpace($env:SUPERCELL_API_TOKEN)
$openaiKeyConfigured = -not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)

Write-Host "Starting Clash Royale Agent backend..."
Write-Host "Health: http://127.0.0.1:$env:RUNTIME_PORT/health"
Write-Host "Python: $pythonExe"
Write-Host "Model: $env:OPENAI_MODEL via $env:OPENAI_WIRE_API"
Write-Host "OpenAI API key configured: $openaiKeyConfigured"
Write-Host "Supercell API token configured: $supercellTokenConfigured"

& $pythonExe runtime_multi.py
