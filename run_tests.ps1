$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Prefer the project virtual environment over the system Python.
$localPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $pythonExe = $localPython
} else {
    $pythonExe = "python"
}

$originalLiveDataEnabled = $env:SUPERCELL_LIVE_DATA_ENABLED
$originalExternalApiRequired = $env:EXTERNAL_API_REQUIRED
$originalSupercellToken = $env:SUPERCELL_API_TOKEN
$originalOpenAIKey = $env:OPENAI_API_KEY

try {
    # Public tests must match GitHub Actions and never depend on a local
    # snapshot or an inherited live-data setting. Tests that cover collection
    # behavior enable it inside their own mock scope.
    $env:SUPERCELL_LIVE_DATA_ENABLED = "false"
    $env:EXTERNAL_API_REQUIRED = "false"
    Remove-Item Env:SUPERCELL_API_TOKEN -ErrorAction SilentlyContinue
    $env:OPENAI_API_KEY = "test-key"

    & $pythonExe -m unittest discover -s tests -t .
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe -m evaluation.run_eval --report evaluation\reports\contract-latest.json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe -m evaluation.run_fault_injection `
        --scenarios evaluation\fault_scenarios.jsonl `
        --report evaluation\reports\fault-injection-latest.json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe -m evaluation.test_inventory --report evaluation\reports\test-inventory-latest.json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if ($env:RUN_LIVE_API_SMOKE -eq "true") {
        $env:SUPERCELL_LIVE_DATA_ENABLED = if ($null -eq $originalLiveDataEnabled) { "true" } else { $originalLiveDataEnabled }
        $env:EXTERNAL_API_REQUIRED = if ($null -eq $originalExternalApiRequired) { "true" } else { $originalExternalApiRequired }
        if ($null -ne $originalSupercellToken) {
            $env:SUPERCELL_API_TOKEN = $originalSupercellToken
        }
        if ($null -ne $originalOpenAIKey) {
            $env:OPENAI_API_KEY = $originalOpenAIKey
        }
        & $pythonExe evaluation\run_live_api_smoke.py
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}
finally {
    if ($null -eq $originalLiveDataEnabled) {
        Remove-Item Env:SUPERCELL_LIVE_DATA_ENABLED -ErrorAction SilentlyContinue
    } else {
        $env:SUPERCELL_LIVE_DATA_ENABLED = $originalLiveDataEnabled
    }
    if ($null -eq $originalExternalApiRequired) {
        Remove-Item Env:EXTERNAL_API_REQUIRED -ErrorAction SilentlyContinue
    } else {
        $env:EXTERNAL_API_REQUIRED = $originalExternalApiRequired
    }
    if ($null -eq $originalSupercellToken) {
        Remove-Item Env:SUPERCELL_API_TOKEN -ErrorAction SilentlyContinue
    } else {
        $env:SUPERCELL_API_TOKEN = $originalSupercellToken
    }
    if ($null -eq $originalOpenAIKey) {
        Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
    } else {
        $env:OPENAI_API_KEY = $originalOpenAIKey
    }
}
