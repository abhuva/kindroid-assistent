[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '../var/voice/kws'
$null = New-Item -ItemType Directory -Force $root
$model = 'sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01'
$archive = Join-Path $root "$model.tar.bz2"
$digest = 'F170013B4716E41B62B9BFD809687C207CEF798EF9BC6534D524E17AF9B6561A'
if (!(Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive).Hash -ne $digest) {
    curl.exe -fsSL --retry 2 --max-time 120 -o $archive "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$model.tar.bz2"
    if ($LASTEXITCODE) { throw 'Could not download the English keyword model.' }
}
if ((Get-FileHash -LiteralPath $archive).Hash -ne $digest) { throw 'Keyword model hash mismatch.' }
$members = @("$model/tokens.txt", "$model/README.md")
foreach ($part in 'encoder', 'decoder', 'joiner') {
    $members += "$model/$part-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
}
tar.exe -xf $archive -C $root @members
if ($LASTEXITCODE) { throw 'Could not install the English keyword model.' }
Write-Host 'Local conversation keyword model ready.'
