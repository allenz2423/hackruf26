# Speak each scene of script.json into narration/<id>.wav with a built-in Windows voice.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File make-narration.ps1 [-Voice "Microsoft Zira Desktop"] [-Rate 0]
param(
  [string]$Voice = "Microsoft Zira Desktop",
  [int]$Rate = 0
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $here "narration"
New-Item -ItemType Directory -Force $out | Out-Null
$scenes = Get-Content (Join-Path $here "script.json") -Raw -Encoding UTF8 | ConvertFrom-Json

# 24 kHz, 16-bit, mono PCM: what render.mjs expects.
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)

foreach ($scene in $scenes) {
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $synth.SelectVoice($Voice)
  $synth.Rate = $Rate
  $path = Join-Path $out ("{0}.wav" -f $scene.id)
  $synth.SetOutputToWaveFile($path, $format)
  $synth.Speak($scene.text)
  $synth.SetOutputToNull()
  $synth.Dispose()
  Write-Output ("spoke {0}" -f $scene.id)
}
