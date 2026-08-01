param(
    [string]$Endpoint = "http://127.0.0.1:8091/snapshot/status",
    [ValidateRange(60, 86400)]
    [int]$IntervalSeconds = 3600,
    [string]$OutputPath = "",
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$uri = [Uri]$Endpoint
if ($uri.Scheme -notin @("http", "https")) {
    throw "Endpoint must use http or https."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repoRoot "logs\snapshot-monitor.jsonl"
}
$outputFile = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $outputFile
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

do {
    $observedAt = [DateTimeOffset]::UtcNow.ToString("o")
    try {
        $status = Invoke-RestMethod -Method Get -Uri $uri.AbsoluteUri -TimeoutSec 30
        $progress = $status.collection_progress
        $record = [ordered]@{
            observed_at = $observedAt
            reachable = $true
            snapshot_status = $status.snapshot_status
            snapshot_id = $status.snapshot_id
            sample_battles = $status.sample_battles
            target_battles = $status.target_battles
            collection_status = $progress.status
            collection_usable_battles = $progress.usable_battles
            collection_fetched_players = $progress.fetched_players
            collection_request_count = $progress.request_count
            collection_rate_limited = $progress.rate_limited
            collection_elapsed_seconds = $progress.elapsed_seconds
            collection_updated_at = $progress.updated_at
            error = $status.error
        }
    }
    catch {
        $record = [ordered]@{
            observed_at = $observedAt
            reachable = $false
            error = $_.Exception.GetType().Name
        }
    }
    Add-Content -LiteralPath $outputFile -Value ($record | ConvertTo-Json -Compress) -Encoding utf8
    if (-not $Once) {
        Start-Sleep -Seconds $IntervalSeconds
    }
} while (-not $Once)
