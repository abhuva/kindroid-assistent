[CmdletBinding()]
param([ValidateSet('multilingual', 'german', 'fluid-speech-pro')][string]$Model = 'multilingual')
$ErrorActionPreference = 'Stop'
$voiceRoot = Join-Path $PSScriptRoot '..\var\voice'
$voicePython = Join-Path $voiceRoot 'runtime\python.exe'
if (!(Test-Path -LiteralPath $voicePython)) {
    throw 'Restore the repository-local voice bundle under var/voice before starting.'
}
$launchArgs = @('-m', 'kindroid_voice')
if ($PSBoundParameters.ContainsKey('Model')) { $launchArgs += @('--model', $Model) }
& $voicePython @launchArgs
if ($LASTEXITCODE -ne 0) { throw 'Voice frontend exited without starting or completing normally.' }
