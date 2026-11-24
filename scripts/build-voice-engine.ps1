[CmdletBinding()]
param([switch]$CloningOnly)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path $repo 'var/voice/native-build'
$source = Join-Path $work 'pocket-tts-4.0.1/rust'
$archive = Join-Path $work 'pocket-tts-v4.0.1.tar.gz'
$null = New-Item -ItemType Directory -Force $work
if (!(Test-Path -LiteralPath $archive)) {
    curl.exe -fsSL --retry 2 --max-time 120 -o $archive 'https://codeload.github.com/kyutai-labs/pocket-tts/tar.gz/refs/tags/v4.0.1'
    if ($LASTEXITCODE) { throw 'Could not download Pocket TTS source.' }
}
if ((Get-FileHash $archive).Hash -ne '4599639EADB01BBBC086DC983E5BE774F22B8DEC9E13769D53899B9DE29C047E') {
    throw 'Pocket TTS source hash mismatch.'
}
tar.exe -xf $archive -C $work
if ($LASTEXITCODE) { throw 'Could not extract Pocket TTS source.' }
$revision = '7efa26973f81bba42cf2ace8da2c1eb9b605d54d'
$encoderArchive = Join-Path $work "xn-ptts-$revision.tar.gz"
if (!(Test-Path -LiteralPath $encoderArchive)) {
    curl.exe -fsSL --retry 2 --max-time 120 -o $encoderArchive "https://codeload.github.com/gabrieldemarmiesse/xn-ptts/tar.gz/$revision"
    if ($LASTEXITCODE) { throw 'Could not download the speaker encoder source.' }
}
if ((Get-FileHash $encoderArchive).Hash -ne '0738AB30DF9669B02D3497866F6FC3CAE9C383A371E5B0736714FACE3BF746F9') {
    throw 'Speaker encoder source hash mismatch.'
}
tar.exe -xf $encoderArchive -C $work
if ($LASTEXITCODE) { throw 'Could not extract the speaker encoder.' }
$encoder = Join-Path $work "xn-ptts-$revision/ptts/src"
$mimi = Join-Path $encoder 'mimi.rs'
$original = [IO.File]::ReadAllText($mimi).Replace("`r`n", "`n")
$forward = @'
let emb = self.encoder.forward(&x, &mut enc_state)?;
        let mut et_state = self.encoder_transformer.init_state(x.dim(0usize)?, 8192)?;
        let mut outs = self.encoder_transformer.forward(&emb, &mut et_state)?;
        let emb = outs.swap_remove(0);
'@
$forward = $forward.Replace("`r`n", "`n")
if (!$original.Contains($forward)) { throw 'Unexpected speaker encoder implementation.' }
Copy-Item -LiteralPath (Join-Path $repo 'tools/voice-engine/encode_blocks.rs') -Destination $encoder -Force
[IO.File]::WriteAllText($mimi, $original.Replace($forward, 'let emb = include!("encode_blocks.rs");'))
Copy-Item -LiteralPath (Join-Path $repo 'tools/voice-engine/main.rs') -Destination (Join-Path $source 'src/main.rs') -Force
foreach ($file in 'Cargo.toml', 'Cargo.lock') {
    Copy-Item -LiteralPath (Join-Path $repo "tools/voice-engine/$file") -Destination (Join-Path $source $file) -Force
}
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (!(Get-Command cmake.exe -ErrorAction SilentlyContinue)) {
    $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.CMake.Project -property installationPath
    if ($vs) { $env:Path = (Join-Path $vs 'Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin') + ';' + $env:Path }
}
if (!$env:CMAKE_GENERATOR -and (Test-Path -LiteralPath $vswhere)) {
    $version = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationVersion
    if ($version -like '16.*') { $env:CMAKE_GENERATOR = 'Visual Studio 16 2019' }
}
$env:CARGO_PROFILE_RELEASE_LTO = 'false'
$env:CARGO_PROFILE_RELEASE_CODEGEN_UNITS = '12'
$env:CARGO_BUILD_JOBS = '2'
$env:CMAKE_BUILD_PARALLEL_LEVEL = '2'
Push-Location $source
try {
    cargo build --release --locked
    if ($LASTEXITCODE) { throw 'Native voice engine build failed.' }
    $destination = Join-Path $repo 'var/pocket-tts/4.0.1'
    $null = New-Item -ItemType Directory -Force $destination
    Copy-Item -LiteralPath 'target/release/pocket-tts.exe' -Destination (Join-Path $destination 'voice-clone.exe') -Force
    if (!$CloningOnly) {
        Copy-Item -LiteralPath 'target/release/pocket-tts.exe' -Destination (Join-Path $destination 'voice-engine.exe') -Force
    }
    Copy-Item -LiteralPath '../LICENSE' -Destination (Join-Path $destination 'voice-engine-LICENSE') -Force
} finally { Pop-Location }
