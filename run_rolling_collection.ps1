param(
    [ValidateSet("daily_ranked", "weekly_expanded")]
    [string]$Mode = "weekly_expanded"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = "python"
}

$persistedSupercellToken = [Environment]::GetEnvironmentVariable("SUPERCELL_API_TOKEN", "User")
if (-not [string]::IsNullOrWhiteSpace($persistedSupercellToken)) {
    $env:SUPERCELL_API_TOKEN = $persistedSupercellToken
}

& $pythonExe -m supercell_preflight --timeout-seconds 20
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $pythonExe (Join-Path $PSScriptRoot "scripts\collect_rolling_corpus.py") --mode $Mode
exit $LASTEXITCODE
