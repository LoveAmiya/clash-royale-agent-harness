param(
    [string]$CoreTaskName = "ClashRoyale-Daily-Ranked-Every-2h",
    [string]$ExpansionTaskName = "ClashRoyale-Expanded-Continuous"
)

$ErrorActionPreference = "Stop"

if ([TimeZoneInfo]::Local.Id -ne "China Standard Time") {
    throw "Parallel collection tasks require the Windows China Standard Time zone."
}

$runner = Join-Path $PSScriptRoot "run_daily_ranked_schedule.ps1"
$coreSupervisor = Join-Path $PSScriptRoot "run_daily_ranked_supervisor.ps1"
$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$startAt = (Get-Date).AddMinutes(1)
$duration = New-TimeSpan -Days 3650

$coreAction = New-ScheduledTaskAction -Execute $powershell -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $coreSupervisor
)
$coreRecoveryInterval = New-TimeSpan -Minutes 15
$coreTrigger = New-ScheduledTaskTrigger -Once -At $startAt -RepetitionInterval $coreRecoveryInterval -RepetitionDuration $duration
$coreSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

$expansionAction = New-ScheduledTaskAction -Execute $powershell -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Mode weekly_expanded -TokenIndex 1' -f $runner
)
$expansionTrigger = New-ScheduledTaskTrigger -Once -At $startAt -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration $duration
$expansionSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 10)

Register-ScheduledTask -TaskName $CoreTaskName -Action $coreAction -Trigger $coreTrigger -Settings $coreSettings -Description "Single core supervisor: top 1000 Path of Legend, two-hour start cadence, coalesced catch-up, token slot 0." -Force | Out-Null
Register-ScheduledTask -TaskName $ExpansionTaskName -Action $expansionAction -Trigger $expansionTrigger -Settings $expansionSettings -Description "Continuous one-hop Path of Legend opponent expansion; token slot 1; single active instance." -Force | Out-Null

Get-ScheduledTask -TaskName $CoreTaskName, $ExpansionTaskName | Select-Object TaskName, State
