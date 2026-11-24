[CmdletBinding()]
param([string]$CloningWeights)

$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '..\var\pocket-tts\4.0.1'
$null = New-Item -ItemType Directory -Path $root -Force
$root = (Resolve-Path $root).Path
$base = 'https://huggingface.co/kyutai/pocket-tts-without-voice-cloning/resolve/a7056f505a4a8ca72d8b5fb1440071d6f1a35739/languages/english_18m_refl_2026-10'
$release = 'pocket-tts-3.3.0-x86_64-pc-windows-msvc'

function Get-Asset([string]$Name, [string]$Url, [string]$Digest) {
    $target = Join-Path $root $Name
    if ((Test-Path -LiteralPath $target) -and (Get-FileHash $target).Hash -eq $Digest) { return }
    Write-Host "Downloading $Name"
    $partial = "$target.$([guid]::NewGuid()).partial"
    try {
        & curl.exe --fail --location --retry 2 --max-time 180 --output $partial $Url
        if ($LASTEXITCODE -ne 0 -or (Get-FileHash $partial).Hash -ne $Digest) {
            throw "Could not verify $Name."
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
    } finally {
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial }
    }
}
Get-Asset 'release.zip' "https://github.com/kyutai-labs/pocket-tts/releases/download/v4.0.1/$release.zip" '98753041f2e65744b989776f87bccb93f1f574a08afe1890c5427ec94cd6309c'
Expand-Archive -LiteralPath (Join-Path $root 'release.zip') -DestinationPath $root -Force
Get-Asset 'model.safetensors' "$base/model.safetensors" '4e7a106c1ac5e3c43226565df57523184e574127c4c30c1ea6aadc4ba5e9bafb'
Get-Asset 'alba.safetensors' "$base/embeddings/alba.safetensors" 'e1aa12e17245952e044853867deef5e77ec4474df119cbeefa4ed2a3d878a7b1'
Get-Asset 'tokenizer.model' "$base/tokenizer.model" 'd461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6'
Get-Asset 'upstream.yaml' 'https://raw.githubusercontent.com/kyutai-labs/pocket-tts/v4.0.1/pocket_tts/config/english_18m_refl_2026-10.yaml' 'c5d424409e3a2a962a27ee20e8655854f0ab3dc15640288e8e86c9e9fee43b01'
$config = [IO.File]::ReadAllText((Join-Path $root 'upstream.yaml')) -replace '(?m)^weights_path: .+$','weights_path: model.safetensors' -replace '(?m)^weights_path_without_voice_cloning: .+$','' -replace '(?m)tokenizer_path: .+$','tokenizer_path: tokenizer.model'
[IO.File]::WriteAllText((Join-Path $root 'english.yaml'), $config)
$smallBase = $base.Replace('english_18m_refl','english_7m_refl')
Get-Asset 'model-7m.safetensors' "$smallBase/model.safetensors" '30df0cd7bf42c0ec0ad40dd33869ed6dfef0a16318ac585f29d95b45d3287dd3'
Get-Asset 'alba-7m.safetensors' "$smallBase/embeddings/alba.safetensors" '2132e156c08f57779a694bb4b57174b1dd8dd1ccb414e8d8df090a648fac13cd'
Get-Asset 'upstream-7m.yaml' 'https://raw.githubusercontent.com/kyutai-labs/pocket-tts/v4.0.1/pocket_tts/config/english_7m_refl_2026-10.yaml' 'da09ff38ac9f818b007e5087b372b11c1bbb3934e3876433ddbd3cd7aad273a7'
$smallConfig = [IO.File]::ReadAllText((Join-Path $root 'upstream-7m.yaml')) -replace '(?m)^weights_path: .+$','weights_path: model-7m.safetensors' -replace '(?m)^weights_path_without_voice_cloning: .+$','' -replace '(?m)tokenizer_path: .+$','tokenizer_path: tokenizer.model'
[IO.File]::WriteAllText((Join-Path $root 'english-7m.yaml'), $smallConfig)
if ($CloningWeights) {
    # Check the official pinned Hugging Face Git LFS pointer, including its content hash.
    $item = Get-Item -LiteralPath $CloningWeights
    $hash = (Get-FileHash -LiteralPath $item.FullName).Hash.ToLowerInvariant()
    $pointer = "version https://git-lfs.github.com/spec/v1`noid sha256:$hash`nsize $($item.Length)`n"
    $blob = [Text.Encoding]::ASCII.GetBytes("blob $($pointer.Length)`0$pointer")
    $sha = [Security.Cryptography.SHA1]::Create()
    try { $oid = [BitConverter]::ToString($sha.ComputeHash($blob)).Replace('-','') } finally { $sha.Dispose() }
    if ($oid -ne '3b9eaa3b931ed651c341cff0573017bd845bbe78') {
        throw 'Select the official 18M REFL cloning weights at the revision linked in README.md.'
    }
    Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $root 'cloning.safetensors') -Force
    [IO.File]::WriteAllText((Join-Path $root 'cloning.yaml'), $config.Replace('weights_path: model.safetensors','weights_path: cloning.safetensors'))
}
Write-Host 'Pocket TTS English is ready.'
