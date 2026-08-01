param(
    [string]$TaskName = "ClashRoyale-Rolling-PathOfLegend",
    [int]$Hour = 3,
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
if ($Hour -lt 0 -or $Hour -gt 23) {
    throw "Hour must be between 0 and 23."
}
$localTimeZone = [TimeZoneInfo]::Local.Id
if ($localTimeZone -ne "China Standard Time") {
    throw "This installer requires the Windows local timezone to be China Standard Time; current=$localTimeZone"
}
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and -not $Replace) {
    throw "Scheduled task already exists. Use -Replace to update it."
}
$scriptPath = Join-Path $PSScriptRoot "run_rolling_schedule.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.AddHours($Hour))
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 24)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Daily expanded Path of Legend collection; target 200,000 unique battles per accepted batch." -Force:$Replace | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
