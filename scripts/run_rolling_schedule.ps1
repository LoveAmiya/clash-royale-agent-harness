$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$mode = "weekly_expanded"

& (Join-Path $projectRoot "run_rolling_collection.ps1") -Mode $mode
exit $LASTEXITCODE
