$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
  throw "Missing backend virtual environment. Run the README first-time setup commands to create backend\.venv and install -e \".[dev]\"."
}
if (-not (Test-Path (Join-Path $frontendDir "node_modules"))) {
  throw "Missing frontend dependencies. Run 'pnpm install --frozen-lockfile' in frontend first."
}
if (-not (Get-Command pnpm.cmd -ErrorAction SilentlyContinue)) {
  throw "pnpm.cmd was not found. Enable Corepack or install pnpm 11, then retry."
}

& $backendPython (Join-Path $root "scripts\dev.py")
exit $LASTEXITCODE
