param([Parameter(Mandatory=$true)][string]$JobFile)
$ErrorActionPreference = 'Stop'
$stockerRoot = Split-Path -Parent $PSScriptRoot
$stockerJobPath = (Resolve-Path -LiteralPath $JobFile).Path
$stockerAllowedRoot = [IO.Path]::GetFullPath((Join-Path $stockerRoot 'reports/private/turnover_jobs')) + [IO.Path]::DirectorySeparatorChar
if (-not $stockerJobPath.StartsWith($stockerAllowedRoot,[StringComparison]::OrdinalIgnoreCase)) { throw 'Private turnover job required' }
Push-Location -LiteralPath $stockerRoot
try {
    $stockerPython = Join-Path $stockerRoot '.venv/Scripts/python.exe'
    $stockerPythonw = Join-Path $stockerRoot '.venv/Scripts/pythonw.exe'
    & $stockerPython -B -m extensions.turnover_job --job $stockerJobPath --check
    if ($LASTEXITCODE -ne 0) { throw 'Turnover integrity check failed' }
    $stockerJob = Get-Content -Raw -Encoding UTF8 -LiteralPath $stockerJobPath | ConvertFrom-Json
    if ($stockerJob.task_name -notmatch '^Stocker-Turnover-[a-f0-9]{16}$') { throw 'Unexpected task name' }
    $stockerArgs = '-B -m extensions.turnover_job --job "{0}"' -f $stockerJobPath
    $stockerExisting = Get-ScheduledTask -TaskName $stockerJob.task_name -ErrorAction SilentlyContinue
    if ($stockerExisting) {
        if ($stockerExisting.Actions.Count -ne 1 -or $stockerExisting.Actions[0].Execute -ne $stockerPythonw -or $stockerExisting.Actions[0].Arguments -ne $stockerArgs) { throw 'Existing task differs' }
    } else {
        $stockerIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $stockerDaily = New-ScheduledTaskTrigger -Daily -At ((Get-Date).AddMinutes(1))
        $stockerRepeat = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 1)
        $stockerDaily.Repetition = $stockerRepeat.Repetition
        $stockerLogon = New-ScheduledTaskTrigger -AtLogOn -User $stockerIdentity
        $stockerAction = New-ScheduledTaskAction -Execute $stockerPythonw -Argument $stockerArgs -WorkingDirectory $stockerRoot
        $stockerPrincipal = New-ScheduledTaskPrincipal -UserId $stockerIdentity -LogonType Interactive -RunLevel Limited
        $stockerSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $stockerJob.task_name -Action $stockerAction -Trigger @($stockerDaily,$stockerLogon) -Principal $stockerPrincipal -Settings $stockerSettings -Description 'Stocker USD0 fixed turnover proxy comparison after original panel and replay audit. No new source API, AI, fitting, locked finals or trades.' | Out-Null
    }
    $stockerTask = Get-ScheduledTask -TaskName $stockerJob.task_name
    $stockerInfo = Get-ScheduledTaskInfo -TaskName $stockerJob.task_name
    $stockerRecord = [ordered]@{task_name=$stockerTask.TaskName;state=$stockerTask.State.ToString();next_run_local=$stockerInfo.NextRunTime.ToString('o');interval_minutes=15;windowless=$true;run_as='current_user_interactive_limited';registered_at=[DateTimeOffset]::UtcNow.ToString('o')}
    [IO.File]::WriteAllText((Join-Path (Split-Path -Parent $stockerJobPath) 'scheduler_registration.json'),($stockerRecord|ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    Export-ScheduledTask -TaskName $stockerJob.task_name | Set-Content -LiteralPath (Join-Path (Split-Path -Parent $stockerJobPath) 'task_definition.xml') -Encoding Unicode
    $stockerRecord | ConvertTo-Json
} finally { Pop-Location }
