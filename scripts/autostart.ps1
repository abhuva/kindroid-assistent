# Sign-in only; never starts or stops the relay as part of installation/removal.
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('Install', 'Remove', 'Status')]
    [string]$Action = 'Status'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $repoRoot '.venv\Scripts\pythonw.exe'
$launcherPath = Join-Path $repoRoot 'scripts\dashboard-autostart.pyw'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$taskName = 'Kindroid Relay - ' + $identity.Name.Split('\')[-1]
$description = "Kindroid Relay sign-in autostart; repository=$repoRoot"
$arguments = '"' + $launcherPath + '"'
$existing = Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -eq $taskName }

if ($existing) {
    # Task Scheduler may normalize a SID to an unqualified local account name.
    $taskOwnerSid = $existing.Principal.UserId
    if ($taskOwnerSid -notmatch '^S-1-') {
        $taskOwnerSid = ([System.Security.Principal.NTAccount]::new($taskOwnerSid)).Translate(
            [System.Security.Principal.SecurityIdentifier]
        ).Value
    }
    $owned = $existing.Description -eq $description -and
        @($existing.Actions).Count -eq 1 -and
        $existing.Actions[0].Execute -eq $pythonPath -and
        $existing.Actions[0].Arguments -eq $arguments -and
        $taskOwnerSid -eq $identity.User.Value
    if (-not $owned) {
        throw "A different task already uses '$taskName'. It has not been changed."
    }
}

if ($Action -eq 'Status') {
    if ($existing) {
        $info = Get-ScheduledTaskInfo -InputObject $existing
        [pscustomobject]@{
            TaskName = $taskName
            State = $existing.State
            LastRunTime = $info.LastRunTime
            LastTaskResult = $info.LastTaskResult
        }
    } else { Write-Output 'Sign-in autostart is not installed.' }
    return
}

if ($Action -eq 'Remove') {
    if ($existing -and $PSCmdlet.ShouldProcess($taskName, 'Remove future sign-in autostart')) {
        Unregister-ScheduledTask -InputObject $existing -Confirm:$false
        Write-Output 'Autostart removed. Any already-running relay was left untouched.'
    } elseif (-not $existing) { Write-Output 'Autostart is already absent.' }
    return
}

if ($existing) {
    Write-Output "Autostart already installed ($($existing.State)); no changes made."
    return
}
foreach ($requiredPath in @($pythonPath, $launcherPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required autostart file is missing: $requiredPath"
    }
}

$taskAction = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $identity.User.Value -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$definition = New-ScheduledTask -Action $taskAction -Trigger $trigger -Principal $principal `
    -Settings $settings -Description $description
if ($PSCmdlet.ShouldProcess($taskName, 'Install current-user sign-in autostart')) {
    Register-ScheduledTask -TaskName $taskName -TaskPath '\' -InputObject $definition | Out-Null
    Write-Output 'Autostart installed for the next sign-in (30-second delay). No relay was started.'
}
