param(
    [switch]$PlanOnly,
    [DateTimeOffset]$RunStartedAt = [DateTimeOffset]::MinValue,
    [DateTimeOffset]$RunFinishedAt = [DateTimeOffset]::MinValue,
    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 120,
    [ValidateRange(1, 300)]
    [int]$SleepSliceSeconds = 30
)

$ErrorActionPreference = "Stop"

function Get-NextRunPlan {
    param(
        [DateTimeOffset]$StartedAt,
        [DateTimeOffset]$FinishedAt,
        [int]$Interval
    )

    $scheduledAt = $StartedAt.AddMinutes($Interval)
    $catchUp = $FinishedAt -ge $scheduledAt
    $nextRunAt = if ($catchUp) { $FinishedAt } else { $scheduledAt }
    $delaySeconds = [Math]::Max(0, [Math]::Ceiling(($nextRunAt - $FinishedAt).TotalSeconds))
    return [ordered]@{
        scheduled_at = $scheduledAt.ToString("o")
        next_run_at = $nextRunAt.ToString("o")
        delay_seconds = [int64]$delaySeconds
        catch_up = $catchUp
    }
}

if ($PlanOnly) {
    if ($RunStartedAt -eq [DateTimeOffset]::MinValue -or $RunFinishedAt -eq [DateTimeOffset]::MinValue) {
        throw "PlanOnly requires RunStartedAt and RunFinishedAt."
    }
    Get-NextRunPlan -StartedAt $RunStartedAt -FinishedAt $RunFinishedAt -Interval $IntervalMinutes |
        ConvertTo-Json -Compress
    exit 0
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "run_daily_ranked_schedule.ps1"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$logDirectory = Join-Path $projectRoot "logs"
$supervisorLog = Join-Path $logDirectory "daily-ranked-supervisor.jsonl"
$lockPath = Join-Path $logDirectory "daily-ranked-supervisor.lock"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-SupervisorEvent {
    param(
        [string]$Status,
        [hashtable]$Fields = @{}
    )

    $record = [ordered]@{
        observed_at = [DateTimeOffset]::Now.ToString("o")
        status = $Status
        supervisor_pid = $PID
    }
    foreach ($key in $Fields.Keys) {
        $record[$key] = $Fields[$key]
    }
    Add-Content -LiteralPath $supervisorLog -Value ($record | ConvertTo-Json -Compress -Depth 6) -Encoding utf8
}

$lockStream = $null
try {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        Write-SupervisorEvent "duplicate_supervisor_skipped" @{
            message = "Another daily ranked supervisor already owns the process lock."
        }
        exit 0
    }

    Write-SupervisorEvent "supervisor_started" @{
        interval_minutes = $IntervalMinutes
        runner = [IO.Path]::GetFileName($runner)
    }

    $sequence = 0
    while ($true) {
        $sequence += 1
        $startedAt = [DateTimeOffset]::Now
        Write-SupervisorEvent "run_started" @{
            sequence = $sequence
            run_started_at = $startedAt.ToString("o")
        }

        $exitCode = 1
        $runnerErrorType = $null
        $runnerErrorMessage = $null
        try {
            $runnerArguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Mode "daily_ranked" -TokenIndex 0' -f $runner
            $runnerProcess = Start-Process -FilePath $powershell -WorkingDirectory $projectRoot -ArgumentList $runnerArguments -WindowStyle Hidden -Wait -PassThru
            $exitCode = $runnerProcess.ExitCode
        }
        catch {
            $runnerErrorType = $_.Exception.GetType().Name
            $runnerErrorMessage = $_.Exception.Message
        }

        $finishedAt = [DateTimeOffset]::Now
        $plan = Get-NextRunPlan -StartedAt $startedAt -FinishedAt $finishedAt -Interval $IntervalMinutes
        $fields = @{
            sequence = $sequence
            exit_code = $exitCode
            run_started_at = $startedAt.ToString("o")
            run_finished_at = $finishedAt.ToString("o")
            duration_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 1)
            scheduled_at = $plan.scheduled_at
            next_run_at = $plan.next_run_at
            delay_seconds = $plan.delay_seconds
            catch_up = $plan.catch_up
        }
        if ($runnerErrorType) { $fields["runner_error_type"] = $runnerErrorType }
        if ($runnerErrorMessage) { $fields["runner_error_message"] = $runnerErrorMessage }
        Write-SupervisorEvent "run_finished" $fields

        while ([DateTimeOffset]::Now -lt [DateTimeOffset]::Parse($plan.next_run_at)) {
            $remaining = ([DateTimeOffset]::Parse($plan.next_run_at) - [DateTimeOffset]::Now).TotalSeconds
            $sleepSeconds = [Math]::Max(1, [Math]::Min($SleepSliceSeconds, [Math]::Ceiling($remaining)))
            Start-Sleep -Seconds $sleepSeconds
        }
    }
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
