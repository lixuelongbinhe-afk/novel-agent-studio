$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root "backend"
$frontendDir = Join-Path $root "frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $backendPython)) {
  $backendPython = "python"
}
$node = "node"
$programFilesNode = Join-Path $env:ProgramFiles "nodejs\node.exe"
if (Test-Path $programFilesNode) {
  $node = $programFilesNode
}
& $env:ComSpec /c "start ""Novel Agent Studio Backend"" /MIN /D ""$backendDir"" ""$backendPython"" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"
& $env:ComSpec /c "start ""Novel Agent Studio Frontend"" /MIN /D ""$frontendDir"" ""$node"" node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173"
Write-Host "Novel Agent Studio started: backend http://127.0.0.1:8000, frontend http://127.0.0.1:5173"
