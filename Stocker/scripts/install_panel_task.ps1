param([Parameter(Mandatory = $true)][string]$JobFile)
$ErrorActionPreference = 'Stop'
$stockerRoot = Split-Path -Parent $PSScriptRoot
$stockerJobPath = (Resolve-Path -LiteralPath $JobFile).Path
$stockerPanelRoot = [IO.Path]::GetFullPath((Join-Path $stockerRoot 'reports/private/historical_panel')) + [IO.Path]::DirectorySeparatorChar
if (-not $stockerJobPath.StartsWith($stockerPanelRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Private historical-panel job required' }
$stockerPython = Join-Path $stockerRoot '.venv/Scripts/python.exe'
$stockerPythonw = Join-Path $stockerRoot '.venv/Scripts/pythonw.exe'
Push-Location -LiteralPath $stockerRoot
try {
    & $stockerPython -B -m research.panel_job --job $stockerJobPath --check
    if ($LASTEXITCODE -ne 0) { throw 'Panel integrity validation failed' }
    $stockerJob = Get-Content -Raw -Encoding UTF8 -LiteralPath $stockerJobPath | ConvertFrom-Json
    if ($stockerJob.task_name -notmatch '^Stocker-HistoricalPanel-[a-f0-9]{16}$') { throw 'Unexpected panel task name' }
    $stockerIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $stockerArguments = '-B -m research.panel_job --job "{0}"' -f $stockerJobPath
    $stockerExisting = Get-ScheduledTask -TaskName $stockerJob.task_name -ErrorAction SilentlyContinue
    if ($stockerExisting) {
        if ($stockerExisting.Actions.Count -ne 1 -or $stockerExisting.Actions[0].Execute -ne $stockerPythonw -or $stockerExisting.Actions[0].Arguments -ne $stockerArguments) {
            throw 'Existing task differs; refusing replacement'
        }
    } else {
        $stockerOnce = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 7)
        $stockerLogon = New-ScheduledTaskTrigger -AtLogOn -User $stockerIdentity
        $stockerAction = New-ScheduledTaskAction -Execute $stockerPythonw -Argument $stockerArguments -WorkingDirectory $stockerRoot
        $stockerPrincipal = New-ScheduledTaskPrincipal -UserId $stockerIdentity -LogonType Interactive -RunLevel Limited
        $stockerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $stockerJob.task_name -Action $stockerAction -Trigger @($stockerOnce, $stockerLogon) -Principal $stockerPrincipal -Settings $stockerSettings -Description 'Stocker personal USD0 historical panel: bounded collection, fixed price-only replay, untouched finals, no trades or model fitting.' | Out-Null
    }
    $stockerTask = Get-ScheduledTask -TaskName $stockerJob.task_name
    $stockerInfo = Get-ScheduledTaskInfo -TaskName $stockerJob.task_name
    $stockerRegistration = [ordered]@{
        task_name = $stockerTask.TaskName
        state = $stockerTask.State.ToString()
        next_run_local = $stockerInfo.NextRunTime.ToString('o')
        run_as = 'current_user_interactive_limited_no_password_stored'
        windowless_python = $true
        wake_to_run = $stockerTask.Settings.WakeToRun
        start_when_available = $stockerTask.Settings.StartWhenAvailable
        retry_minutes = 5
        registered_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
    $stockerJson = $stockerRegistration | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path (Split-Path -Parent $stockerJobPath) 'scheduler_registration.json'), $stockerJson, (New-Object Text.UTF8Encoding($false)))
    Export-ScheduledTask -TaskName $stockerJob.task_name | Set-Content -LiteralPath (Join-Path (Split-Path -Parent $stockerJobPath) 'task_definition.xml') -Encoding Unicode
    $stockerJson
} finally { Pop-Location }
