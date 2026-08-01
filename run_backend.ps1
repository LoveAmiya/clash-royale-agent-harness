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
$runtimeRole = if ($env:RUNTIME_ROLE) { $env:RUNTIME_ROLE.ToLowerInvariant() } else { "all" }

# Provider routing is fixed for this application. The key remains external.
$env:OPENAI_BASE_URL = "https://crs.ruinique.com"
$env:OPENAI_WIRE_API = "responses"
$env:OPENAI_MODEL = "gpt-5.5"
$env:OPENAI_REVIEW_MODEL = "gpt-5.5"
$env:OPENAI_REASONING_EFFORT = "medium"
$env:PARSER_REASONING_EFFORT = "medium"
$env:SYNTHESIS_REASONING_EFFORT = "medium"
if (-not $env:EXTERNAL_API_REQUIRED) {
    $env:EXTERNAL_API_REQUIRED = "true"
}
$env:SUPERCELL_CACHE_TTL_SECONDS = "86400"

# A Codex-launched shell may retain an older process value after the Windows
# user environment is updated. Treat the persisted user OpenAI key as the
# canonical local credential so a backend restart always picks up rotations.
# Never print token values.
$persistedSupercellToken = [Environment]::GetEnvironmentVariable("SUPERCELL_API_TOKEN", "User")
$persistedOpenAIKey = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
if (-not [string]::IsNullOrWhiteSpace($persistedOpenAIKey)) {
    $env:OPENAI_API_KEY = $persistedOpenAIKey
}
if ($runtimeRole -eq "collector" -and -not [string]::IsNullOrWhiteSpace($persistedSupercellToken)) {
    $env:SUPERCELL_API_TOKEN = $persistedSupercellToken
} elseif ([string]::IsNullOrWhiteSpace($env:SUPERCELL_API_TOKEN)) {
    if (-not [string]::IsNullOrWhiteSpace($persistedSupercellToken)) {
        $env:SUPERCELL_API_TOKEN = $persistedSupercellToken
    }
}

$supercellTokenConfigured = -not [string]::IsNullOrWhiteSpace($env:SUPERCELL_API_TOKEN)
$openaiKeyConfigured = -not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)

$skipSupercellPreflight = $env:SUPERCELL_PREFLIGHT_SKIP -in @("1", "true", "TRUE", "yes", "YES")
if ($runtimeRole -eq "collector" -and -not $skipSupercellPreflight) {
    Write-Host "Running Supercell collector preflight..."
    & $pythonExe -m supercell_preflight
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Supercell collector preflight failed. Update the API token IP allowlist before starting collection."
        exit $LASTEXITCODE
    }
}

Write-Host "Starting Clash Royale Agent backend..."
Write-Host "Health: http://127.0.0.1:$env:RUNTIME_PORT/health"
Write-Host "Python: $pythonExe"
Write-Host "Model: $env:OPENAI_MODEL via $env:OPENAI_WIRE_API"
Write-Host "OpenAI API key configured: $openaiKeyConfigured"
Write-Host "Supercell API token configured: $supercellTokenConfigured"

& $pythonExe runtime_multi.py
