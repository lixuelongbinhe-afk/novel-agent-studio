$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDist = Join-Path $root "frontend\dist"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
  $backendPython = "python"
}
if (-not (Test-Path (Join-Path $frontendDist "index.html"))) {
  throw "Missing frontend production build. Run pnpm.cmd run build in frontend first."
}
$env:NAS_ENV = "production"
$env:NAS_FRONTEND_DIST = $frontendDist
$env:NAS_CORS_ORIGINS = ""
$env:NAS_ALLOWED_HOSTS = "127.0.0.1,localhost"
Write-Host "Novel Agent Studio: http://127.0.0.1:8000"
Push-Location $backendDir
try {
  & $backendPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-server-header
} finally {
  Pop-Location
}
