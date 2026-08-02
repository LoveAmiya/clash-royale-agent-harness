$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

# Prefer the project virtual environment over the system Python.
$localPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $pythonExe = $localPython
} else {
    $pythonExe = "python"
}

function Resolve-RagDocumentsPath {
    $legacyPath = Join-Path $PSScriptRoot "data\rag_documents.json"
    if (Test-Path $legacyPath) {
        return $legacyPath
    }

    $activeGroupPath = Join-Path $PSScriptRoot "data\active_snapshot_group.json"
    if (Test-Path $activeGroupPath) {
        try {
            $activeGroup = Get-Content -Raw -LiteralPath $activeGroupPath | ConvertFrom-Json
            if ($activeGroup.snapshot_group_id) {
                $groupDocs = Join-Path $PSScriptRoot ("data\snapshot_groups\{0}\rag_documents.json" -f $activeGroup.snapshot_group_id)
                if (Test-Path $groupDocs) {
                    return $groupDocs
                }
            }
        } catch {
            Write-Warning "Unable to read active snapshot group pointer: $($_.Exception.Message)"
        }
    }

    $officialPointerPath = Join-Path $PSScriptRoot "data\official_snapshot_pointer.json"
    if (Test-Path $officialPointerPath) {
        try {
            $officialPointer = Get-Content -Raw -LiteralPath $officialPointerPath | ConvertFrom-Json
            if ($officialPointer.snapshot_id) {
                $archiveDocs = Join-Path $PSScriptRoot ("data\snapshot_archives\{0}\rag_documents.json" -f $officialPointer.snapshot_id)
                if (Test-Path $archiveDocs) {
                    return $archiveDocs
                }
            }
        } catch {
            Write-Warning "Unable to read official snapshot pointer: $($_.Exception.Message)"
        }
    }

    throw "No RAG documents found. Expected data\rag_documents.json, active snapshot group documents, or official snapshot archive documents."
}

$originalLiveDataEnabled = $env:SUPERCELL_LIVE_DATA_ENABLED
$originalExternalApiRequired = $env:EXTERNAL_API_REQUIRED
$originalSupercellToken = $env:SUPERCELL_API_TOKEN
$originalOpenAIKey = $env:OPENAI_API_KEY

try {
    # Unit tests and deterministic evaluation must never inherit local API
    # credentials. Live-data behavior remains enabled because its tests replace
    # the client and token with deterministic mocks.
    $env:SUPERCELL_LIVE_DATA_ENABLED = "true"
    $env:EXTERNAL_API_REQUIRED = "false"
    Remove-Item Env:SUPERCELL_API_TOKEN -ErrorAction SilentlyContinue
    $env:OPENAI_API_KEY = "test-key"

    & $pythonExe -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe -m evaluation.run_eval
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $ragDocumentsPath = Resolve-RagDocumentsPath
    & $pythonExe -m evaluation.citation_benchmark `
        --documents $ragDocumentsPath `
        --report evaluation\reports\citation-latest.json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $pythonExe -m evaluation.run_fault_injection `
        --scenarios evaluation\fault_scenarios.jsonl `
        --report evaluation\reports\fault-injection-latest.json
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if ($env:RUN_RAG_RETRIEVAL_BENCHMARK -eq "true") {
        & $pythonExe -m evaluation.retrieval_benchmark `
            --report evaluation\reports\retrieval-latest.json
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
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
