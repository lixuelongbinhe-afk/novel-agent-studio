$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backendPython = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
  $backendPython = "python"
}
& $backendPython (Join-Path $root "desktop\launcher.py")
