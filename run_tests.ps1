$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Prefer the project virtual environment over the system Python.
$localPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $pythonExe = $localPython
} else {
    $pythonExe = "python"
}

& $pythonExe -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $pythonExe -m evaluation.run_eval
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($env:RUN_LIVE_API_SMOKE -eq "true") {
    & $pythonExe evaluation\run_live_api_smoke.py
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
