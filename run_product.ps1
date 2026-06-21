$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "$Root\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
  $Python = "C:\Users\shubh\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
}

if (-not (Test-Path -LiteralPath $Python)) {
  $Python = "python"
}

Set-Location -LiteralPath $Root
& $Python server.py
