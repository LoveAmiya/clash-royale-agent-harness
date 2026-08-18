param(
    [switch]$Full,
    [switch]$SkipTests,
    [switch]$SkipRuff,
    [switch]$IncludeUnstaged
)

$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $projectRoot
$script:ChecksRun = @()
$script:ChecksSkipped = @()
$script:PrivatePathScanSummary = "not run"

$localPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $localPython) {
    $pythonExe = $localPython
} else {
    $pythonExe = "python"
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    Write-Host "==> $Name"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
    $script:ChecksRun += $Name
}

Invoke-Step "staged whitespace diff check" {
    git diff --cached --check
}

if ($IncludeUnstaged) {
    Invoke-Step "worktree whitespace diff check" {
        git diff --check
    }
}

$trackedFiles = @(git ls-files)
$privatePathPattern = '^data/(?!card_aliases\.zh-CN\.json$)|^logs/|^tmp/|^trace/|^traces/|^benchmark/|^benchmarks/|^benchmark-results/|\.env($|\.)|\.sqlite3?$|\.db$|\.jsonl$|(^|/)__pycache__/|\.pyc$|^evaluation/reports/|^evaluation/live_|^evaluation/.*report.*\.json$|^evaluation/.*\.(stdout|stderr)\.|\.zip$|\.key$|\.pem$'
$allowedJsonlFixtures = @("evaluation/cases.jsonl", "evaluation/fault_scenarios.jsonl")
$trackedPrivatePaths = @(
    $trackedFiles |
        Where-Object {
            $_ -match $privatePathPattern -and
            $_ -notin $allowedJsonlFixtures -and
            $_ -ne ".env.example"
        }
)
if ($trackedPrivatePaths.Count -gt 0) {
    Write-Error ("Tracked private/generated paths detected:" + [Environment]::NewLine + ($trackedPrivatePaths -join [Environment]::NewLine))
}
$script:PrivatePathScanSummary = "tracked private/generated paths: 0"

$mustBeIgnored = @(
    "data/corpus/corpus.sqlite",
    "data/rolling_lanes/private.json",
    "logs/runtime.log",
    "tmp/work.json",
    "evaluation/rolling_retrieval_benchmark_report.json",
    "evaluation/reports/local.json",
    "trace/session.jsonl",
    "benchmarks/run.json",
    "local_dump.jsonl",
    "archive.zip"
)

foreach ($path in $mustBeIgnored) {
    git check-ignore -q -- $path
    if ($LASTEXITCODE -ne 0) {
        throw "Expected path is not ignored: $path"
    }
}

$mustRemainTrackable = @(
    "data/card_aliases.zh-CN.json",
    "evaluation/cases.jsonl",
    "evaluation/fault_scenarios.jsonl"
)

foreach ($path in $mustRemainTrackable) {
    git check-ignore -q -- $path
    if ($LASTEXITCODE -eq 0) {
        throw "Public fixture is unexpectedly ignored: $path"
    }
}

$secretPattern = 'token|secret|api[_-]?key|password'
$secretLikeDiff = @(git diff --cached --name-only | Select-String -Pattern $secretPattern -CaseSensitive:$false)
if ($secretLikeDiff.Count -gt 0) {
    Write-Error ("Secret-like staged paths detected:" + [Environment]::NewLine + ($secretLikeDiff -join [Environment]::NewLine))
}

if ($SkipRuff) {
    $script:ChecksSkipped += "ruff lint (disabled by -SkipRuff)"
} elseif (-not $SkipRuff) {
    & $pythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('ruff') else 1)"
    if ($LASTEXITCODE -eq 0) {
        Invoke-Step "ruff lint" {
            & $pythonExe -m ruff check .
        }
    } else {
        Write-Warning "ruff is not installed in the selected Python environment; skipping ruff lint"
        $script:ChecksSkipped += "ruff lint (not installed)"
        $global:LASTEXITCODE = 0
    }
}

$rootPythonFiles = @(Get-ChildItem -LiteralPath $projectRoot -File -Filter "*.py" | ForEach-Object { $_.Name })
$compileTargets = @("src", "evaluation", "harness", "planner", "skills", "tests") + $rootPythonFiles
Invoke-Step "python compileall" {
    & $pythonExe -m compileall -q @compileTargets
}

if (-not $SkipTests) {
    if ($Full) {
        Invoke-Step "full public gate" {
            powershell -ExecutionPolicy Bypass -File .\run_tests.ps1
        }
    } else {
        Invoke-Step "unit discovery" {
            & $pythonExe -m unittest discover -s tests
        }
    }
}

Write-Host "Repository checks passed."
Write-Host ("Summary: checks run: " + ($script:ChecksRun -join ", "))
Write-Host ("Summary: checks skipped: " + ($(if ($script:ChecksSkipped.Count -gt 0) { $script:ChecksSkipped -join ", " } else { "none" })))
Write-Host ("Summary: private path scan: " + $script:PrivatePathScanSummary)
