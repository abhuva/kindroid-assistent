[CmdletBinding()]
param([string]$SourceDirectory)

$ErrorActionPreference = 'Stop'
$profilePath = Join-Path $PSScriptRoot '../src/kindroid_voice/fluid-speech-pro.json'
$profile = Get-Content -LiteralPath $profilePath -Encoding UTF8 -Raw | ConvertFrom-Json
$modelPath = Join-Path $PSScriptRoot '../var/voice/dictate/models/fluid-speech-pro'
$modelPath = [IO.Path]::GetFullPath($modelPath)
if ($SourceDirectory) {
    $SourceDirectory = (Resolve-Path -LiteralPath $SourceDirectory).Path
}
$null = New-Item -ItemType Directory -Path $modelPath -Force

function Test-ModelFile($Path, $File) {
    return (Test-Path -LiteralPath $Path -PathType Leaf) -and
        ((Get-Item -LiteralPath $Path).Length -eq $File.bytes) -and
        ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $File.sha256)
}

$completed = 0
$total = $profile.files.Count
foreach ($file in $profile.files) {
    $destination = Join-Path $modelPath $file.name
    Write-Host "Fluid Speech Pro $completed of $total files verified. Preparing $($file.name)."
    if (!(Test-ModelFile $destination $file)) {
        $upstream = if ($file.upstream) { $file.upstream } else { $file.name }
        $partial = "$destination.$([guid]::NewGuid().ToString('N')).download"
        try {
            if ($SourceDirectory) {
                Copy-Item -LiteralPath (Join-Path $SourceDirectory $upstream) -Destination $partial
            } else {
                $url = "$($profile.source)/resolve/$($profile.revision)/$upstream"
                & curl.exe --fail --location --progress-bar --connect-timeout 15 --max-time 900 --retry 2 --output $partial $url
                if ($LASTEXITCODE -ne 0) { throw "Could not download $upstream." }
            }
            if (!(Test-ModelFile $partial $file)) { throw "Integrity check failed for $upstream." }
            if (Test-Path -LiteralPath $destination -PathType Leaf) {
                [IO.File]::Replace($partial, $destination, [NullString]::Value)
            } else {
                [IO.File]::Move($partial, $destination)
            }
        } finally {
            if (Test-Path -LiteralPath $partial -PathType Leaf) {
                Remove-Item -LiteralPath $partial
            }
        }
    }
    $completed++
}
Write-Host "Fluid Speech Pro ready. $completed of $total files verified."
