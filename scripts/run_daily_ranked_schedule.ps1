param(
    [switch]$DryRun,
    [switch]$NotifyTest,
    [ValidateSet("daily_ranked", "weekly_expanded")]
    [string]$Mode = "daily_ranked",
    [int]$TokenIndex = -1
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$taskName = if ($Mode -eq "daily_ranked") { "ClashRoyale-Daily-Ranked-Every-2h" } else { "ClashRoyale-Expanded-Continuous" }
$taskId = if ($Mode -eq "daily_ranked") { "daily_ranked_every_2h" } else { "weekly_expanded_continuous" }
$resolvedTokenIndex = if ($TokenIndex -ge 0) { $TokenIndex } elseif ($Mode -eq "daily_ranked") { 0 } else { 1 }
$logDirectory = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$scheduleLog = Join-Path $logDirectory ("{0}-schedule.jsonl" -f ($Mode -replace "_", "-"))
$runtimeTemp = Join-Path $projectRoot ("tmp\collector-runtime\{0}" -f $Mode)
New-Item -ItemType Directory -Path $runtimeTemp -Force | Out-Null
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
$env:TMPDIR = $runtimeTemp
$env:SQLITE_TMPDIR = $runtimeTemp
$runtimeTempRoot = [IO.Path]::GetPathRoot($runtimeTemp).TrimEnd("\")
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Write-ScheduleEvent {
    param(
        [string]$Status,
        [hashtable]$Fields = @{}
    )

    $record = [ordered]@{
        observed_at = [DateTimeOffset]::Now.ToString("o")
        task = $taskId
        collection_mode = $Mode
        status = $Status
    }
    foreach ($key in $Fields.Keys) {
        $record[$key] = $Fields[$key]
    }
    Add-Content -LiteralPath $scheduleLog -Value ($record | ConvertTo-Json -Compress -Depth 8) -Encoding utf8
}

function Get-PythonExe {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    return "python"
}

function Get-EnvSetting {
    param([string[]]$Names)

    foreach ($scope in @("User", "Process", "Machine")) {
        foreach ($name in $Names) {
            $value = [Environment]::GetEnvironmentVariable($name, $scope)
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return [pscustomobject]@{
                    Name = $name
                    Scope = $scope
                    Value = $value
                }
            }
        }
    }
    return $null
}

function Get-SupercellTokens {
    $setting = Get-EnvSetting @("SUPERCELL_API_TOKENS")
    $tokens = @()
    if ($setting -and -not [string]::IsNullOrWhiteSpace($setting.Value)) {
        $raw = $setting.Value.Trim()
        if ($raw.StartsWith("[")) {
            try {
                $items = @($raw | ConvertFrom-Json)
            }
            catch {
                throw "SUPERCELL_API_TOKENS is not valid JSON."
            }
        }
        else {
            $items = @($raw -split "[,;`r`n]+")
        }
        $tokens = @($items | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim() })
        if ($tokens.Count -gt 1) {
            return [pscustomobject]@{
                Values = $tokens
                Raw = $setting.Value
                Name = $setting.Name
                Scope = $setting.Scope
            }
        }
    }

    $legacy = Get-EnvSetting @("SUPERCELL_API_TOKEN")
    if (
        $tokens.Count -eq 1 -and
        $legacy -and
        -not [string]::IsNullOrWhiteSpace($legacy.Value) -and
        $tokens[0] -ne $legacy.Value.Trim()
    ) {
        $combined = @($legacy.Value.Trim(), $tokens[0])
        return [pscustomobject]@{
            Values = $combined
            Raw = ($combined | ConvertTo-Json -Compress)
            Name = "combined-token-variables"
            Scope = "User"
        }
    }
    if ($tokens.Count -gt 0) {
        return [pscustomobject]@{
            Values = $tokens
            Raw = $setting.Value
            Name = $setting.Name
            Scope = $setting.Scope
        }
    }
    if ($legacy -and -not [string]::IsNullOrWhiteSpace($legacy.Value)) {
        return [pscustomobject]@{
            Values = @($legacy.Value.Trim())
            Raw = $null
            Name = $legacy.Name
            Scope = $legacy.Scope
        }
    }
    return $null
}

function Convert-BytesToGiB {
    param([Nullable[Int64]]$Bytes)

    if ($null -eq $Bytes) {
        return $null
    }
    return [Math]::Round(($Bytes / 1GB), 2)
}

function Get-StagingStats {
    $lanePath = Join-Path $projectRoot ("data\rolling_lanes\{0}" -f $Mode)
    $sizeBytes = [int64]0
    if (Test-Path -LiteralPath $lanePath) {
        $measurement = Get-ChildItem -LiteralPath $lanePath -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum
        if ($null -ne $measurement.Sum) {
            $sizeBytes = [int64]$measurement.Sum
        }
    }
    $limitBytes = if ($Mode -eq "daily_ranked") { [int64]512MB } else { [int64]4GB }
    return [pscustomobject]@{
        bytes = $sizeBytes
        gib = Convert-BytesToGiB $sizeBytes
        limit_bytes = $limitBytes
        limit_gib = Convert-BytesToGiB $limitBytes
    }
}

function Get-CorpusStats {
    param([string]$PythonExe)

    $dbPath = Join-Path $projectRoot "data\corpus\corpus.sqlite"
    if (-not (Test-Path -LiteralPath $dbPath)) {
        return [pscustomobject]@{
            unique_battle_facts = $null
            battle_observations = $null
            complete_loadout_rows = $null
            corpus_db_size_bytes = $null
            corpus_db_size_gib = $null
            stats_error = "corpus.sqlite missing"
        }
    }

    $python = @'
import json
import os
import sqlite3

result = {}
db_path = os.path.join("data", "corpus", "corpus.sqlite")
queries = [
    ("unique_battle_facts", "select count(*) from battles"),
    ("battle_observations", "select count(*) from battle_observations"),
    ("complete_loadout_rows", "select count(*) from battle_loadouts where complete=1"),
]

try:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        for name, sql in queries:
            result[name] = conn.execute(sql).fetchone()[0]
    finally:
        conn.close()
    size = os.path.getsize(db_path)
    result["corpus_db_size_bytes"] = size
    result["corpus_db_size_gib"] = round(size / (1024 ** 3), 3)
except Exception as exc:
    result["unique_battle_facts"] = None
    result["battle_observations"] = None
    result["complete_loadout_rows"] = None
    result["corpus_db_size_bytes"] = None
    result["corpus_db_size_gib"] = None
    result["stats_error"] = str(exc)

print(json.dumps(result, ensure_ascii=True))
'@

    try {
        $raw = $python | & $PythonExe -
        if ($LASTEXITCODE -ne 0) {
            throw "python stats exited with $LASTEXITCODE"
        }
        return $raw | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            unique_battle_facts = $null
            battle_observations = $null
            complete_loadout_rows = $null
            corpus_db_size_bytes = $null
            corpus_db_size_gib = $null
            stats_error = $_.Exception.Message
        }
    }
}

function Get-CollectionStatus {
    $statusPath = Join-Path $projectRoot ("data\corpus\collection_status.{0}.json" -f $Mode)
    if (-not (Test-Path -LiteralPath $statusPath)) {
        $statusPath = Join-Path $projectRoot "data\corpus\collection_status.json"
    }
    if (-not (Test-Path -LiteralPath $statusPath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            status_error = $_.Exception.Message
        }
    }
}

function Get-PendingPublicationStatus {
    $statusPath = Join-Path $projectRoot "data\corpus\collection_status.json"
    if (-not (Test-Path -LiteralPath $statusPath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            status_error = $_.Exception.Message
        }
    }
}

function Get-TaskSnapshot {
    try {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $info = $task | Get-ScheduledTaskInfo -ErrorAction Stop
        return [pscustomobject]@{
            state = [string]$task.State
            last_run_time = if ($info.LastRunTime) { $info.LastRunTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
            next_run_time = if ($info.NextRunTime) { $info.NextRunTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
            last_task_result = $info.LastTaskResult
        }
    }
    catch {
        return [pscustomobject]@{
            task_error = $_.Exception.Message
        }
    }
}

function Get-DeltaValue {
    param(
        [object]$Before,
        [object]$After,
        [string]$Property
    )

    if ($null -eq $Before -or $null -eq $After) {
        return $null
    }
    if ($null -eq $Before.$Property -or $null -eq $After.$Property) {
        return $null
    }
    return ([int64]$After.$Property - [int64]$Before.$Property)
}

function New-NotificationContent {
    param(
        [string]$Status,
        [hashtable]$Fields,
        [object]$StatsBefore,
        [object]$StatsAfter,
        [object]$CollectionStatus,
        [object]$TaskSnapshot,
        [string]$Severity
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("Collection task status: $Status")
    $lines.Add("Severity: $Severity")
    $lines.Add("Time: " + [DateTimeOffset]::Now.ToString("yyyy-MM-dd HH:mm:ss zzz"))
    if ($Fields.ContainsKey("batch_id")) { $lines.Add("batch_id: " + $Fields["batch_id"]) }
    if ($Fields.ContainsKey("exit_code")) { $lines.Add("exit_code: " + $Fields["exit_code"]) }
    if ($Fields.ContainsKey("error_type")) { $lines.Add("error_type: " + $Fields["error_type"]) }
    if ($Fields.ContainsKey("duration_seconds")) { $lines.Add("duration_seconds: " + $Fields["duration_seconds"]) }
    if ($Fields.ContainsKey("current_ip")) { $lines.Add("current_public_ip: " + $Fields["current_ip"]) }
    if ($Fields.ContainsKey("preflight_status")) { $lines.Add("preflight_status: " + $Fields["preflight_status"]) }
    if ($Fields.ContainsKey("official_probe_status")) { $lines.Add("official_probe_status: " + $Fields["official_probe_status"]) }
    if ($Fields.ContainsKey("current_ip_allowed")) { $lines.Add("ip_allowlist_matched: " + $Fields["current_ip_allowed"]) }
    if ($Fields.ContainsKey("free_bytes")) { $lines.Add("free_f_drive_gib: " + (Convert-BytesToGiB $Fields["free_bytes"])) }
    if ($Fields.ContainsKey("staging_gib")) { $lines.Add("lane_staging_gib: " + $Fields["staging_gib"]) }
    if ($Fields.ContainsKey("staging_limit_gib")) { $lines.Add("lane_staging_limit_gib: " + $Fields["staging_limit_gib"]) }
    if ($Fields.ContainsKey("message")) { $lines.Add("message: " + $Fields["message"]) }

    $lines.Add("")
    $lines.Add("Current data totals:")
    $lines.Add("deduped_battles: " + $StatsAfter.unique_battle_facts)
    $lines.Add("pre_dedupe_observations: " + $StatsAfter.battle_observations)
    $lines.Add("complete_loadout_rows: " + $StatsAfter.complete_loadout_rows)
    $lines.Add("corpus_sqlite_gib: " + $StatsAfter.corpus_db_size_gib)
    if ($StatsAfter.stats_error) { $lines.Add("stats_error: " + $StatsAfter.stats_error) }

    $deltaUnique = Get-DeltaValue $StatsBefore $StatsAfter "unique_battle_facts"
    $deltaObserved = Get-DeltaValue $StatsBefore $StatsAfter "battle_observations"
    $deltaLoadouts = Get-DeltaValue $StatsBefore $StatsAfter "complete_loadout_rows"
    if ($null -ne $deltaUnique -or $null -ne $deltaObserved -or $null -ne $deltaLoadouts) {
        $lines.Add("")
        $lines.Add("This run delta:")
        $lines.Add("deduped_battles_delta: " + $deltaUnique)
        $lines.Add("pre_dedupe_observations_delta: " + $deltaObserved)
        $lines.Add("complete_loadout_rows_delta: " + $deltaLoadouts)
    }

    if ($CollectionStatus) {
        $lines.Add("")
        $lines.Add("collection_status:")
        if ($CollectionStatus.status) { $lines.Add("status: " + $CollectionStatus.status) }
        if ($CollectionStatus.batch_id) { $lines.Add("batch_id: " + $CollectionStatus.batch_id) }
        if ($CollectionStatus.error_type) { $lines.Add("collector_error_type: " + $CollectionStatus.error_type) }
        if ($CollectionStatus.message) { $lines.Add("collector_error_message: " + $CollectionStatus.message) }
        if ($null -ne $CollectionStatus.usable_battles) { $lines.Add("usable_battles: " + $CollectionStatus.usable_battles) }
        if ($CollectionStatus.validation -and $CollectionStatus.validation.failures) {
            $lines.Add("validation_failures: " + (@($CollectionStatus.validation.failures) -join ","))
        }
        if ($CollectionStatus.publication_error) {
            if ($CollectionStatus.publication_error.error_type) { $lines.Add("publication_error_type: " + $CollectionStatus.publication_error.error_type) }
            if ($CollectionStatus.publication_error.message) { $lines.Add("publication_error_message: " + $CollectionStatus.publication_error.message) }
        }
        if ($CollectionStatus.updated_at) { $lines.Add("updated_at: " + $CollectionStatus.updated_at) }
    }

    if ($TaskSnapshot) {
        $lines.Add("")
        $lines.Add("scheduled_task:")
        if ($TaskSnapshot.state) { $lines.Add("state: " + $TaskSnapshot.state) }
        if ($TaskSnapshot.next_run_time) { $lines.Add("next_run_time: " + $TaskSnapshot.next_run_time) }
        if ($null -ne $TaskSnapshot.last_task_result) { $lines.Add("last_task_result: " + $TaskSnapshot.last_task_result) }
        if ($TaskSnapshot.task_error) { $lines.Add("task_error: " + $TaskSnapshot.task_error) }
    }

    $lines.Add("")
    $lines.Add("Security note: no Supercell token, PushPlus token, player tags, raw battles, or full logs are included.")
    return ($lines -join [Environment]::NewLine)
}

function Send-PushPlusNotification {
    param(
        [string]$Title,
        [string]$Content
    )

    $providerSetting = Get-EnvSetting @("COLLECT_NOTIFY_PROVIDER")
    $provider = "pushplus"
    if ($providerSetting -and -not [string]::IsNullOrWhiteSpace($providerSetting.Value)) {
        $provider = $providerSetting.Value
    }
    if ($provider.ToLowerInvariant() -ne "pushplus") {
        return [pscustomobject]@{
            status = "skipped_provider"
            provider = $provider
        }
    }

    $tokenSetting = Get-EnvSetting @("COLLECT_NOTIFY_TOKEN", "Pushplus", "PUSHPLUS_TOKEN", "PUSH_PLUS_TOKEN", "PUSHPLUS_USER_TOKEN", "PUSHPLUS_MESSAGE_TOKEN")
    if ($null -eq $tokenSetting -or [string]::IsNullOrWhiteSpace($tokenSetting.Value)) {
        return [pscustomobject]@{
            status = "missing_token"
            provider = "pushplus"
        }
    }

    $payload = @{
        token = $tokenSetting.Value
        title = $Title
        content = $Content
        template = "txt"
        channel = "wechat"
    } | ConvertTo-Json -Depth 4

    try {
        $response = Invoke-RestMethod -Uri "https://www.pushplus.plus/send" -Method Post -ContentType "application/json; charset=utf-8" -Body $payload -TimeoutSec 20
        return [pscustomobject]@{
            status = if ($response.code -eq 200) { "sent" } else { "failed" }
            provider = "pushplus"
            code = $response.code
            message = $response.msg
            token_variable = $tokenSetting.Name
            token_scope = $tokenSetting.Scope
        }
    }
    catch {
        return [pscustomobject]@{
            status = "error"
            provider = "pushplus"
            message = $_.Exception.Message
            token_variable = $tokenSetting.Name
            token_scope = $tokenSetting.Scope
        }
    }
}

function Write-TerminalStatus {
    param(
        [string]$Status,
        [hashtable]$Fields = @{},
        [object]$StatsBefore = $null,
        [string]$Severity = "info",
        [string]$Title = $null,
        [bool]$SendNotification = $true
    )

    $statsAfter = Get-CorpusStats $pythonExe
    $staging = Get-StagingStats
    $collectionStatus = Get-CollectionStatus
    $taskSnapshot = Get-TaskSnapshot

    $Fields["stats_unique_battle_facts"] = $statsAfter.unique_battle_facts
    $Fields["stats_battle_observations"] = $statsAfter.battle_observations
    $Fields["stats_complete_loadout_rows"] = $statsAfter.complete_loadout_rows
    $Fields["corpus_db_size_gib"] = $statsAfter.corpus_db_size_gib
    $Fields["staging_gib"] = $staging.gib
    $Fields["staging_limit_gib"] = $staging.limit_gib
    $Fields["delta_unique_battle_facts"] = Get-DeltaValue $StatsBefore $statsAfter "unique_battle_facts"
    $Fields["delta_battle_observations"] = Get-DeltaValue $StatsBefore $statsAfter "battle_observations"
    $Fields["delta_complete_loadout_rows"] = Get-DeltaValue $StatsBefore $statsAfter "complete_loadout_rows"
    $Fields["runtime_temp_root"] = $runtimeTempRoot
    if ($collectionStatus -and $collectionStatus.status) { $Fields["collection_status"] = $collectionStatus.status }
    if ($taskSnapshot -and $taskSnapshot.next_run_time) { $Fields["next_run_time"] = $taskSnapshot.next_run_time }

    if ([string]::IsNullOrWhiteSpace($Title)) {
        $Title = "Collector status: $Status"
    }

    if ($SendNotification) {
        $content = New-NotificationContent -Status $Status -Fields $Fields -StatsBefore $StatsBefore -StatsAfter $statsAfter -CollectionStatus $collectionStatus -TaskSnapshot $taskSnapshot -Severity $Severity
        $notify = Send-PushPlusNotification -Title $Title -Content $content
        $Fields["notify_status"] = $notify.status
        if ($notify.code) { $Fields["notify_code"] = $notify.code }
        if ($notify.message) { $Fields["notify_message"] = $notify.message }
        if ($notify.token_variable) { $Fields["notify_token_variable"] = $notify.token_variable }
        if ($notify.token_scope) { $Fields["notify_token_scope"] = $notify.token_scope }
    }
    else {
        $Fields["notify_status"] = "suppressed_non_failure"
    }

    Write-ScheduleEvent $Status $Fields
}

$pythonExe = Get-PythonExe
$batchId = "{0}-{1:yyyyMMdd-HHmmss}" -f $Mode, (Get-Date)
$runStartedAt = Get-Date
$statsBefore = $null

try {
    $drive = Get-PSDrive -Name F
    $freeBytes = [int64]$drive.Free

    if ($NotifyTest) {
        $statsBefore = Get-CorpusStats $pythonExe
        Write-TerminalStatus "notify_test" @{
            batch_id = $batchId
            free_bytes = $freeBytes
            message = "PushPlus notification test only; collection was not started."
        } $statsBefore "info" "Collector PushPlus notification test" $true
        exit 0
    }

    $activeWriters = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.ProcessId -ne $PID -and
                $_.CommandLine -match "collect_rolling_corpus\.py" -and
                $_.CommandLine.Contains($Mode)
            } |
            Select-Object -ExpandProperty ProcessId
    )
    if ($activeWriters.Count -gt 0) {
        $statsBefore = Get-CorpusStats $pythonExe
        Write-TerminalStatus "skipped_active_writer" @{
            process_ids = ($activeWriters -join ",")
            batch_id = $batchId
            free_bytes = $freeBytes
            message = "Another collection writer is already running."
        } $statsBefore "warn" "Collector skipped: active writer" $false
        exit 0
    }

    $minimumFreeBytes = [int64]20GB
    if ($freeBytes -lt $minimumFreeBytes) {
        $statsBefore = Get-CorpusStats $pythonExe
        Write-TerminalStatus "skipped_low_disk" @{
            batch_id = $batchId
            free_bytes = $freeBytes
            minimum_free_bytes = $minimumFreeBytes
            message = "Free disk space is below the safety threshold."
        } $statsBefore "error" "Collector failed: low disk space" $true
        exit 5
    }

    $pendingPublication = Get-PendingPublicationStatus
    if ($pendingPublication -and $pendingPublication.status -eq "accepted_publication_failed") {
        $statsBefore = Get-CorpusStats $pythonExe
        $collector = ".\scripts\collect_rolling_corpus.py"
        $repairRaw = & $pythonExe $collector --mode $Mode --retry-publication-only
        $repairExit = $LASTEXITCODE
        $repair = $null
        try {
            $repair = $repairRaw | ConvertFrom-Json
        }
        catch {
            $repair = $null
        }
        if ($repairExit -eq 4) {
            Write-TerminalStatus "publication_repair_deferred" @{
                batch_id = $pendingPublication.batch_id
                exit_code = $repairExit
                free_bytes = $freeBytes
                message = "Publication repair is waiting for the corpus writer."
            } $statsBefore "info" "Collector publication repair deferred" $false
            exit 4
        }
        if ($repairExit -ne 0) {
            Write-TerminalStatus "publication_repair_failed" @{
                batch_id = $pendingPublication.batch_id
                exit_code = $repairExit
                free_bytes = $freeBytes
                error_type = if ($repair) { $repair.error_type } else { "UnparsedPublicationRepairError" }
                message = if ($repair) { $repair.message } else { "Publication repair returned unreadable output." }
            } $statsBefore "error" "Collector failed: publication repair" $true
            exit $repairExit
        }
        Write-ScheduleEvent "publication_repaired" @{
            batch_id = $pendingPublication.batch_id
            repaired_by_mode = $Mode
            runtime_temp_root = $runtimeTempRoot
            free_bytes = $freeBytes
        }
    }

    $supercellTokens = Get-SupercellTokens
    if ($null -eq $supercellTokens -or $supercellTokens.Values.Count -le $resolvedTokenIndex) {
        $statsBefore = Get-CorpusStats $pythonExe
        Write-TerminalStatus "skipped_token_missing" @{
            batch_id = $batchId
            free_bytes = $freeBytes
            token_index = $resolvedTokenIndex
            available_token_count = if ($supercellTokens) { $supercellTokens.Values.Count } else { 0 }
            message = "The configured Supercell token list does not contain the token required by this lane."
        } $statsBefore "error" "Collector failed: Supercell token missing" $true
        exit 2
    }
    if ($supercellTokens.Raw) {
        $env:SUPERCELL_API_TOKENS = $supercellTokens.Raw
    }
    $env:SUPERCELL_API_TOKEN = $supercellTokens.Values[$resolvedTokenIndex]

    $preflightRaw = & $pythonExe -m supercell_preflight --timeout-seconds 20
    $preflightExit = $LASTEXITCODE
    $preflight = $null
    try {
        $preflight = $preflightRaw | ConvertFrom-Json
    }
    catch {
        $preflight = $null
    }
    if ($preflightExit -ne 0 -or $null -eq $preflight -or $preflight.ready -ne $true) {
        $statsBefore = Get-CorpusStats $pythonExe
        Write-TerminalStatus "skipped_preflight_failed" @{
            batch_id = $batchId
            current_ip = if ($preflight) { $preflight.current_ip } else { $null }
            current_ip_allowed = if ($preflight) { $preflight.current_ip_allowed } else { $null }
            official_probe_status = if ($preflight) { $preflight.official_probe_status } else { $null }
            preflight_status = if ($preflight) { $preflight.status } else { "unparsed" }
            free_bytes = $freeBytes
            message = "Supercell preflight failed."
        } $statsBefore "error" "Collector failed: Supercell preflight" $true
        exit $preflightExit
    }

    $statsBefore = Get-CorpusStats $pythonExe

    if ($DryRun) {
        Write-TerminalStatus "dry_run_ready" @{
            batch_id = $batchId
            current_ip = $preflight.current_ip
            free_bytes = $freeBytes
            message = "Dry run passed; collection was not started."
        } $statsBefore "info" "Collector dry run ready" $false
        exit 0
    }

    $stdout = Join-Path $logDirectory "rolling-$Mode-$stamp.stdout.log"
    $stderr = Join-Path $logDirectory "rolling-$Mode-$stamp.stderr.log"
    Write-ScheduleEvent "starting" @{
        batch_id = $batchId
        stdout = $stdout
        stderr = $stderr
        current_ip = $preflight.current_ip
        free_bytes = $freeBytes
        stats_unique_battle_facts = $statsBefore.unique_battle_facts
        stats_battle_observations = $statsBefore.battle_observations
        stats_complete_loadout_rows = $statsBefore.complete_loadout_rows
        runtime_temp_root = $runtimeTempRoot
    }

    $collector = ".\scripts\collect_rolling_corpus.py"
    $process = Start-Process $pythonExe -WorkingDirectory $projectRoot -ArgumentList @($collector, "--mode", $Mode, "--batch-id", $batchId) -RedirectStandardOutput $stdout -RedirectStandardError $stderr -Wait -PassThru
    $durationSeconds = [Math]::Round(((Get-Date) - $runStartedAt).TotalSeconds, 1)
    $isDeferredMerge = $process.ExitCode -eq 4
    $finishStatus = if ($isDeferredMerge) { "deferred_merge" } else { "finished" }
    $finishSeverity = if ($process.ExitCode -eq 0 -or $isDeferredMerge) { "info" } else { "error" }
    $finishTitle = if ($isDeferredMerge) { "Collector deferred merge: $Mode" } elseif ($process.ExitCode -eq 0) { "Collector succeeded: $Mode" } else { "Collector failed: $Mode" }
    $sendFinishNotification = $process.ExitCode -ne 0 -and -not $isDeferredMerge

    Write-TerminalStatus $finishStatus @{
        batch_id = $batchId
        exit_code = $process.ExitCode
        stdout = $stdout
        stderr = $stderr
        current_ip = $preflight.current_ip
        current_ip_allowed = $preflight.current_ip_allowed
        preflight_status = $preflight.status
        free_bytes = $freeBytes
        duration_seconds = $durationSeconds
    } $statsBefore $finishSeverity $finishTitle $sendFinishNotification
    exit $process.ExitCode
}
catch {
    $durationSeconds = [Math]::Round(((Get-Date) - $runStartedAt).TotalSeconds, 1)
    Write-TerminalStatus "error" @{
        batch_id = $batchId
        error_type = $_.Exception.GetType().Name
        message = $_.Exception.Message
        duration_seconds = $durationSeconds
    } $statsBefore "error" "Collector script error" $true
    exit 1
}
