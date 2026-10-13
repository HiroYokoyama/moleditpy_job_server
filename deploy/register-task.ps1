param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$Config
)
$ErrorActionPreference = 'Stop'
$taskPython = (Resolve-Path -LiteralPath $Python).Path
$taskConfig = (Resolve-Path -LiteralPath $Config).Path
$action = New-ScheduledTaskAction -Execute $taskPython -Argument "-m job_server serve --config `"$taskConfig`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'MoleditPy Job Server' -Action $action -Trigger $trigger -Principal $principal -Settings $settings
