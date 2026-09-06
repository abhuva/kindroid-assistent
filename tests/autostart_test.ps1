# Real Windows task-definition builders, fake registration: no task/process writes.
$ErrorActionPreference = 'Stop'
# Import constructors before installing mocks; module auto-loading would otherwise
# replace the shadowed registration commands in a fresh PowerShell process.
Import-Module ScheduledTasks
$testState = @{ registered = $null; registrations = 0; removals = 0 }
function Get-ScheduledTask { param($TaskPath) $testState.registered }
function Register-ScheduledTask {
    param($TaskName, $TaskPath, $InputObject)
    $testState.registrations++
    # Windows commonly reads back a short local name rather than the supplied SID.
    $testState.registered = [pscustomobject]@{
        TaskName = $TaskName
        Description = $InputObject.Description
        Actions = $InputObject.Actions
        Settings = $InputObject.Settings
        Triggers = $InputObject.Triggers
        State = 'Ready'
        Principal = [pscustomobject]@{
            UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name.Split('\')[-1]
            LogonType = $InputObject.Principal.LogonType
            RunLevel = $InputObject.Principal.RunLevel
        }
    }
}
function Unregister-ScheduledTask { param($InputObject, $Confirm) $testState.removals++; $testState.registered = $null }
function Start-ScheduledTask { throw 'Installation must not start the relay' }
function Stop-ScheduledTask { throw 'Removal must not stop the relay' }
function Check($condition, $message) { if (-not $condition) { throw $message } }
$installer = Join-Path $PSScriptRoot '..\scripts\autostart.ps1'
& $installer -Action Install -WhatIf
Check ($testState.registrations -eq 0) 'WhatIf registered a task'
& $installer -Action Install
Check ($testState.registrations -eq 1) 'Task was not registered once'
Check ($testState.registered.Principal.LogonType -eq 'Interactive') 'Not sign-in scoped'
Check ($testState.registered.Principal.RunLevel -eq 'Limited') 'Unexpected elevation'
Check ($testState.registered.Settings.ExecutionTimeLimit -eq 'PT0S') 'Time limit enabled'
Check ($testState.registered.Settings.MultipleInstances -eq 'IgnoreNew') 'Duplicates allowed'
Check ($testState.registered.Settings.RestartCount -eq 3) 'Unexpected restart policy'
Check ($testState.registered.Triggers[0].Delay -eq 'PT30S') 'Missing sign-in delay'
& $installer -Action Install
Check ($testState.registrations -eq 1) 'Repeated install rewrote the task'
$savedDescription = $testState.registered.Description
$testState.registered.Description = 'Unrelated user-owned task'
$refused = $false
try { & $installer -Action Remove } catch { $refused = $true }
Check $refused 'Unrelated task was accepted'
Check ($testState.removals -eq 0) 'Unrelated task was removed'
$testState.registered.Description = $savedDescription
& $installer -Action Remove -WhatIf
Check ($testState.removals -eq 0) 'WhatIf removed a task'
& $installer -Action Remove
& $installer -Action Remove
Check ($testState.removals -eq 1) 'Remove was not idempotent'
Write-Output 'Autostart task-definition and ownership checks passed.'
