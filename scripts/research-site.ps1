param(
    [ValidateSet('Setup','Check','Build','Preview','New','Publish')][string]$Action = 'Preview',
    [string]$Title = '',
    [switch]$NoOpen
)
$ErrorActionPreference = 'Stop'
$ResearchRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ResearchRoot
$ResearchPython = Join-Path $ResearchRoot 'var/docs-venv/Scripts/python.exe'
$ResearchRun = Join-Path $ResearchRoot ('var/research-site/runs/' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ResearchRun -Force | Out-Null
$ResearchStep = 0
$ResearchLock = $null

function Set-ResearchStatus([string]$State, [string]$Stage) {
    @{state=$State; stage=$Stage; logs=$ResearchRun; updated=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ResearchRoot 'var/research-site/latest.json') -Encoding UTF8
    Write-Host $Stage
}

function Invoke-ResearchChild([string]$Executable, [string[]]$Arguments, [int]$Seconds = 120, [switch]$Capture) {
    $script:ResearchStep += 1
    $stdout = Join-Path $ResearchRun ($script:ResearchStep.ToString() + '.out')
    $stderr = Join-Path $ResearchRun ($script:ResearchStep.ToString() + '.err')
    $child = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $ResearchRoot `
        -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $handle = $child.Handle
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    $seen = 0
    try {
        do {
            $done = $child.WaitForExit(500)
            if (-not $Capture) {
                $lines = @(Get-Content -LiteralPath $stdout -ErrorAction SilentlyContinue)
                if ($lines.Count -gt $seen) { $lines | Select-Object -Skip $seen | ForEach-Object { Write-Host $_ }; $seen=$lines.Count }
            }
            if (-not $done -and [DateTime]::UtcNow -ge $deadline) { throw 'Documentation step exceeded its hard time limit.' }
        } while (-not $done)
        if ($child.ExitCode -ne 0) { Get-Content -LiteralPath $stderr | Write-Host; throw "Documentation step failed. Logs: $ResearchRun" }
        if ($Capture) { Get-Content -LiteralPath $stdout }
    } finally {
        if (-not $child.HasExited) { Stop-Process -Id $child.Id -Force }
    }
}

function Test-ResearchGit {
    if ($LASTEXITCODE -ne 0) { throw 'Publication Git operation failed; no application changes were staged.' }
}

function Build-ResearchSite {
    Set-ResearchStatus 'running' 'Checking the public source boundary and privacy rules...'
    Invoke-ResearchChild $ResearchPython @('scripts/test-public-site.py') 30
    Invoke-ResearchChild $ResearchPython @('scripts/check-public-site.py') 30
    Set-ResearchStatus 'running' 'Building curated public pages with Zensical...'
    Invoke-ResearchChild $ResearchPython @('-m','zensical','build','--clean','--strict') 120
    Set-ResearchStatus 'running' 'Checking generated pages and search data...'
    Invoke-ResearchChild $ResearchPython @('scripts/check-public-site.py','--built') 30
}

try {
    $ResearchLock = [IO.File]::Open((Join-Path $ResearchRoot 'var/research-site/operation.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    Set-ResearchStatus 'running' ("Starting research-site operation: $Action. Logs: $ResearchRun")
    if ($Action -eq 'New') {
        if (-not $Title) { $Title = Read-Host 'Title for your private draft' }
        if (-not $Title.Trim() -or $Title.Length -gt 160 -or $Title -match '[\r\n]') { throw 'Use a one-line title of up to 160 characters.' }
        $slug = ($Title.ToLowerInvariant() -replace '[^a-z0-9]+','-').Trim('-')
        if (-not $slug) { $slug='research-note' }
        $date = Get-Date -Format 'yyyy-MM-dd'
        $draftDir = Join-Path $ResearchRoot 'var/journal-drafts'
        New-Item -ItemType Directory -Path $draftDir -Force | Out-Null
        $draft = Join-Path $draftDir ("$date-$slug.md")
        if (Test-Path -LiteralPath $draft) { throw 'A draft with that date/title already exists.' }
        $text = "---`ndraft: true`ndate: $date`n---`n`n# $Title`n`n## Question`n`n## Setup`n`n## Observation`n`n## Limits`n`n## Next step`n"
        [IO.File]::WriteAllText($draft,$text,[Text.UTF8Encoding]::new($false))
        Set-ResearchStatus 'complete' ("Private draft created: $draft. This is excluded from publication.")
        if (-not $NoOpen) { Start-Process -FilePath 'notepad.exe' -ArgumentList ('"' + $draft + '"') -WindowStyle Normal }
        return
    }
    if ($Action -eq 'Setup') {
        if (-not (Test-Path -LiteralPath $ResearchPython)) {
            Set-ResearchStatus 'running' 'Creating an isolated documentation environment...'
            Invoke-ResearchChild (Get-Command python.exe).Source @('-m','venv','var/docs-venv') 60
        }
        Set-ResearchStatus 'running' 'Installing pinned public documentation packages...'
        Invoke-ResearchChild $ResearchPython @('-m','pip','--isolated','install','--index-url','https://pypi.org/simple','-r','requirements-docs.txt') 180
        Set-ResearchStatus 'complete' 'Documentation environment is ready.'
        return
    }
    if (-not (Test-Path -LiteralPath $ResearchPython)) { throw 'Run scripts/research-site.ps1 -Action Setup first.' }
    if ($Action -eq 'Check') {
        Invoke-ResearchChild $ResearchPython @('scripts/test-public-site.py') 30
        Invoke-ResearchChild $ResearchPython @('scripts/check-public-site.py') 30
    } else { Build-ResearchSite }
    if ($Action -eq 'Preview') {
        Set-ResearchStatus 'running' 'Preview: http://127.0.0.1:8008/ . Close this console to stop; maximum session is four hours.'
        Invoke-ResearchChild $ResearchPython @('scripts/serve-public-site.py') 14400
    }
    if ($Action -eq 'Publish') {
        Set-ResearchStatus 'running' 'Preparing an isolated docs-only Git snapshot; application changes stay untouched...'
        $files = @(Invoke-ResearchChild $ResearchPython @('scripts/check-public-site.py','--list') 30 -Capture)
        $relativeClone = 'var/research-site/publish-' + [guid]::NewGuid().ToString('N')
        $clone = [IO.Path]::GetFullPath((Join-Path $ResearchRoot $relativeClone))
        $allowedPrefix = [IO.Path]::GetFullPath((Join-Path $ResearchRoot 'var/research-site')) + [IO.Path]::DirectorySeparatorChar
        if (-not $clone.StartsWith($allowedPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe publication workspace.' }
        $remote='https://github.com/abhuva/kindroid-assistent.git'
        $exists = @(git ls-remote --heads $remote docs-site); Test-ResearchGit
        if ($exists.Count) {
            git -c credential.helper= -c 'credential.helper=!gh auth git-credential' clone --quiet --single-branch --branch docs-site $remote $clone
            Test-ResearchGit
        } else {
            git init --quiet --initial-branch=docs-site $clone; Test-ResearchGit
            git -C $clone remote add origin $remote; Test-ResearchGit
        }
        $oldFiles = @(git -C $clone ls-files); Test-ResearchGit
        foreach ($name in $oldFiles) {
            $target=[IO.Path]::GetFullPath((Join-Path $clone $name))
            if (-not $target.StartsWith($clone + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe existing publication path.' }
            if (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target }
        }
        git -C $clone rm -r --cached --ignore-unmatch . | Out-Null; Test-ResearchGit
        foreach ($name in $files) {
            $target=Join-Path $clone $name
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath (Join-Path $ResearchRoot $name) -Destination $target
        }
        $pathspec=Join-Path $ResearchRun 'public-paths.txt'
        [IO.File]::WriteAllLines($pathspec,$files,[Text.UTF8Encoding]::new($false))
        git -C $clone add --pathspec-from-file=$pathspec; Test-ResearchGit
        Invoke-ResearchChild $ResearchPython @('scripts/check-public-site.py','--root',$relativeClone,'--publication-tree') 30
        git -C $clone diff --cached --quiet
        if ($LASTEXITCODE -eq 0) { Set-ResearchStatus 'complete' 'Public source is already current; nothing was pushed.'; return }
        if ($LASTEXITCODE -ne 1) { throw 'Cannot review publication changes.' }
        git -C $clone config user.name abhuva; Test-ResearchGit
        git -C $clone config user.email 2739380+abhuva@users.noreply.github.com; Test-ResearchGit
        git -C $clone commit --quiet -m 'Update public research notes'; Test-ResearchGit
        Set-ResearchStatus 'running' ("Publishing $($files.Count) reviewed files to docs-site; GitHub Pages progress is available in Actions...")
        git -C $clone -c credential.helper= -c 'credential.helper=!gh auth git-credential' push --quiet origin docs-site
        Test-ResearchGit
    }
    Set-ResearchStatus 'complete' ("Research-site operation completed: $Action")
} catch {
    Set-ResearchStatus 'failed' 'Research-site operation failed. No application changes were staged. Check the retained logs.'
    throw
} finally {
    if ($ResearchLock) { $ResearchLock.Dispose() }
}
