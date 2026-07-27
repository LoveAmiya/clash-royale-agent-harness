param(
    [string]$ComposeProject = "",
    [switch]$KeepStack
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "compose.production.yml"
$drillComposeFile = Join-Path $PSScriptRoot "compose.alert-drill.yml"
$reportDirectory = Join-Path $PSScriptRoot "evaluation\reports"
$reportPath = Join-Path $reportDirectory ("alert-drill-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
if ([string]::IsNullOrWhiteSpace($ComposeProject)) {
    $ComposeProject = "cr-agent-alert-drill-" + (Get-Date -Format "yyyyMMddHHmmss")
}
$report = [ordered]@{
    started_at = [DateTimeOffset]::UtcNow.ToString("o")
    status = "running"
    received_total = 0
    last_received_at = $null
    error = $null
}

try {
    docker compose -p $ComposeProject -f $composeFile up --build -d alert-webhook alertmanager
    if ($LASTEXITCODE -ne 0) { throw "Alert pipeline failed to start" }

    $baseline = Invoke-RestMethod -Uri "http://127.0.0.1:8094/alerts/stats"
    $baselineTotal = [int]$baseline.received_total

    docker compose -p $ComposeProject -f $composeFile -f $drillComposeFile up -d prometheus
    if ($LASTEXITCODE -ne 0) { throw "Prometheus drill rule failed to start" }

    $deadline = (Get-Date).AddSeconds(90)
    do {
        Start-Sleep -Seconds 1
        $stats = Invoke-RestMethod -Uri "http://127.0.0.1:8094/alerts/stats"
        if ($stats.received_total -gt $baselineTotal) {
            $report.status = "passed"
            $report.received_total = $stats.received_total
            $report.last_received_at = $stats.last_received_at
            Write-Host "Alert pipeline passed: received_total=$($stats.received_total), last_received_at=$($stats.last_received_at)"
            return
        }
    } while ((Get-Date) -lt $deadline)

    throw "Alertmanager did not deliver the Prometheus drill alert within 90 seconds"
} catch {
    $report.status = "failed"
    $report.error = $_.Exception.Message
    throw
} finally {
    $report.finished_at = [DateTimeOffset]::UtcNow.ToString("o")
    New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
    ConvertTo-Json -InputObject $report -Depth 6 | Set-Content -Path $reportPath -Encoding UTF8
    Write-Host "Alert drill report: $reportPath"
    if (-not $KeepStack) {
        docker compose -p $ComposeProject -f $composeFile -f $drillComposeFile down --volumes | Out-Null
    }
}
