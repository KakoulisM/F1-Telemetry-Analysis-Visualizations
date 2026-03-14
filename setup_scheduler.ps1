# F1 Scheduler - Windows Setup
# Run this in PowerShell as Administrator

$scriptPath = (Get-Location).Path
$pythonExe = (Get-Command python).Source
$schedulerScript = Join-Path $scriptPath "scheduler.py"

# Create the scheduled task for continuous mode
$taskName = "F1-Telemetry-Scheduler"
$description = "Automatically runs F1 telemetry pipeline after sessions complete (smart scheduling based on F1 calendar)"

# Task runs at startup and stays running
$trigger = New-ScheduledTaskTrigger -AtStartup

$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument "$schedulerScript --mode continuous" `
    -WorkingDirectory $scriptPath

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

# Create the task
try {
    Register-ScheduledTask `
        -TaskName $taskName `
        -Description $description `
        -Trigger $trigger `
        -Action $action `
        -Settings $settings `
        -Force
    
    Write-Host "✓ Task created successfully: $taskName" -ForegroundColor Green
    Write-Host "  - Runs continuously in smart mode"
    Write-Host "  - Only checks around scheduled F1 sessions"
    Write-Host "  - Auto-restarts on failure"
    Write-Host "  - Logs: $scriptPath\logs\"
    Write-Host ""
    Write-Host "Start task now: Start-ScheduledTask -TaskName '$taskName'"
    Write-Host "View task: taskschd.msc"
    Write-Host "Or run: Get-ScheduledTask -TaskName '$taskName'"
}
catch {
    Write-Host "❌ Failed to create task: $_" -ForegroundColor Red
    Write-Host "Make sure you're running PowerShell as Administrator"
}
