param(
    [double]$TimeoutSeconds = 20,
    [switch]$PreferUserToken
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$localPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $pythonExe = $localPython
} else {
    $pythonExe = "python"
}

$persistedSupercellToken = [Environment]::GetEnvironmentVariable("SUPERCELL_API_TOKEN", "User")
if ($PreferUserToken -and -not [string]::IsNullOrWhiteSpace($persistedSupercellToken)) {
    $env:SUPERCELL_API_TOKEN = $persistedSupercellToken
} elseif ([string]::IsNullOrWhiteSpace($env:SUPERCELL_API_TOKEN) -and -not [string]::IsNullOrWhiteSpace($persistedSupercellToken)) {
    $env:SUPERCELL_API_TOKEN = $persistedSupercellToken
}

& $pythonExe -m supercell_preflight --timeout-seconds $TimeoutSeconds
exit $LASTEXITCODE
