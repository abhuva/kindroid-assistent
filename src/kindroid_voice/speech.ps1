$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$installed = @([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers())
$preferred = $installed | Where-Object { $_.Culture.Name -eq [cultureinfo]::CurrentCulture.Name } | Select-Object -First 1
if (-not $preferred) { $preferred = $installed | Select-Object -First 1 }
if (-not $preferred) { throw 'Install a Windows desktop speech recognition language.' }
$ear = [System.Speech.Recognition.SpeechRecognitionEngine]::new($preferred)
try {
    $phrase = [System.Speech.Recognition.GrammarBuilder]::new()
    $phrase.Culture = $preferred.Culture
    $phrase.Append('Hannah')
    $ear.LoadGrammar([System.Speech.Recognition.Grammar]::new($phrase))
    $ear.SetInputToDefaultAudioDevice()
    [Console]::Out.WriteLine('READY')
    [Console]::Out.Flush()
    do { $heard = $ear.Recognize([TimeSpan]::FromSeconds(2)) }
    until ($heard -and $heard.Text -eq 'Hannah' -and $heard.Confidence -ge 0.62)
} finally { $ear.Dispose() }
