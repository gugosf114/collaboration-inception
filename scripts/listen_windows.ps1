param(
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
try {
    $recognizer.SetInputToDefaultAudioDevice()
    $recognizer.LoadGrammar(
        (New-Object System.Speech.Recognition.DictationGrammar)
    )
    $result = $recognizer.Recognize(
        [TimeSpan]::FromSeconds($TimeoutSeconds)
    )
    if ($null -eq $result -or [string]::IsNullOrWhiteSpace($result.Text)) {
        throw "No speech was recognized."
    }
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $result.Text
}
finally {
    $recognizer.Dispose()
}
