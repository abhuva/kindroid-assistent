param(
    [string[]]$TestFiles = @('tests'),
    [ValidateRange(1,60)][int]$TimeoutSeconds = 30
)
$ErrorActionPreference = 'Stop'
$testDir = Join-Path ([System.IO.Path]::GetTempPath()) ('kindroid-tests-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $testDir | Out-Null
$testOut = Join-Path $testDir 'stdout.log'
$testErr = Join-Path $testDir 'stderr.log'
$testArgs = @('-m', 'pytest') + $TestFiles + @('-vv', '--tb=short')
$testPython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
$p = Start-Process -FilePath $testPython -ArgumentList $testArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $testOut -RedirectStandardError $testErr
$testHandle = $p.Handle
$finished = $p.WaitForExit($TimeoutSeconds * 1000)
if (-not $finished) { Stop-Process -Id $p.Id -Force }
Get-Content -LiteralPath $testOut -Tail 70
Get-Content -LiteralPath $testErr
Write-Output "Captured logs: $testDir"
if (-not $finished) { throw "pytest exceeded $TimeoutSeconds seconds" }
if ($p.ExitCode -ne 0) { throw "pytest failed with exit code $($p.ExitCode)" }
