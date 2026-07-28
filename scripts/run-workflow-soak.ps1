param(
    [ValidateRange(1, 480)]
    [int]$Minutes = 120,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$artifacts = Join-Path $root "artifacts"
New-Item -ItemType Directory -Force -Path $artifacts | Out-Null

$startedAt = Get-Date
$deadline = $startedAt.AddMinutes($Minutes)
$iterations = 0

Push-Location $backend
try {
    while ((Get-Date) -lt $deadline) {
        & $Python -m pytest -q `
            tests/test_database_writer.py `
            tests/test_workflow_streaming.py `
            tests/test_workflow_scheduler.py `
            tests/test_phase5_workflows.py `
            -k "eight_streaming or cancel or bounded or shutdown or forced_process"
        if ($LASTEXITCODE -ne 0) {
            throw "Soak iteration $iterations failed with exit code $LASTEXITCODE"
        }
        $iterations += 1
    }
}
finally {
    Pop-Location
}

$finishedAt = Get-Date
$report = [ordered]@{
    started_at = $startedAt.ToUniversalTime().ToString("o")
    finished_at = $finishedAt.ToUniversalTime().ToString("o")
    requested_minutes = $Minutes
    elapsed_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    successful_iterations = $iterations
}
$report | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $artifacts "workflow-soak.json")
Write-Host "Completed $iterations workflow soak iterations."
