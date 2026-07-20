$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root "backend\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
  $pythonw = "pythonw"
}
& $pythonw (Join-Path $root "desktop\launcher.py")
