param(
    [string]$CoreTaskName = "ClashRoyale-Daily-Ranked-Every-2h",
    [string]$ExpansionTaskName = "ClashRoyale-Expanded-Continuous",
    [string]$TaskUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
    [string[]]$LegacyVisibleTaskNames = @(
        "ClashRoyale-Daily-Ranked-Noon",
        "ClashRoyale-Rolling-PathOfLegend",
        "ClashRoyale-Rolling-Collection"
    )
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
$principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType S4U -RunLevel Limited

$coreAction = New-ScheduledTaskAction -Execute $powershell -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $coreSupervisor
)
$coreRecoveryInterval = New-TimeSpan -Minutes 15
$coreTrigger = New-ScheduledTaskTrigger -Once -At $startAt -RepetitionInterval $coreRecoveryInterval -RepetitionDuration $duration
$coreSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

$expansionAction = New-ScheduledTaskAction -Execute $powershell -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Mode weekly_expanded -TokenIndex 1' -f $runner
)
$expansionTrigger = New-ScheduledTaskTrigger -Once -At $startAt -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration $duration
$expansionSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 10) -Hidden

$legacyCleanup = foreach ($legacyTaskName in $LegacyVisibleTaskNames) {
    $legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
    if ($null -eq $legacyTask) {
        [pscustomobject]@{ TaskName = $legacyTaskName; Status = "not_found" }
        continue
    }
    try {
        Disable-ScheduledTask -TaskName $legacyTaskName -ErrorAction Stop | Out-Null
        Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false -ErrorAction Stop
        [pscustomobject]@{ TaskName = $legacyTaskName; Status = "removed" }
    } catch {
        [pscustomobject]@{ TaskName = $legacyTaskName; Status = "cleanup_failed"; Error = $_.Exception.Message }
    }
}

Register-ScheduledTask -TaskName $CoreTaskName -Action $coreAction -Trigger $coreTrigger -Settings $coreSettings -Principal $principal -Description "Single core supervisor: top 1000 Path of Legend, two-hour start cadence, coalesced catch-up, token slot 0." -Force | Out-Null
Register-ScheduledTask -TaskName $ExpansionTaskName -Action $expansionAction -Trigger $expansionTrigger -Settings $expansionSettings -Principal $principal -Description "Continuous one-hop Path of Legend opponent expansion; token slot 1; single active instance." -Force | Out-Null

Get-ScheduledTask -TaskName $CoreTaskName, $ExpansionTaskName |
    Select-Object TaskName, State, @{Name = "Hidden"; Expression = { $_.Settings.Hidden } }, @{Name = "LogonType"; Expression = { $_.Principal.LogonType } }

$legacyCleanup
