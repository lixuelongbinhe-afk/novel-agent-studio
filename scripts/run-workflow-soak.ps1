param(
    [ValidateRange(1, 480)]
    [int]$Minutes = 120,
    [string]$Python = "python",
    [ValidateRange(0, 100000)]
    [int]$MaxIterations = 0
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$artifacts = Join-Path $root "artifacts"
New-Item -ItemType Directory -Force -Path $artifacts | Out-Null

$startedAt = Get-Date
$deadline = $startedAt.AddMinutes($Minutes)
$iterations = 0
$failure = $null

try {
    Push-Location $backend
    try {
        while (
            (Get-Date) -lt $deadline -and
            ($MaxIterations -eq 0 -or $iterations -lt $MaxIterations)
        ) {
            & $Python -m pytest -q `
                tests/test_database_writer.py `
                tests/test_workflow_streaming.py `
                tests/test_workflow_scheduler.py `
                tests/test_workflow_runtime.py `
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
}
catch {
    $failure = $_
}

$finishedAt = Get-Date
$report = [ordered]@{
    status = if ($null -eq $failure) { "passed" } else { "failed" }
    started_at = $startedAt.ToUniversalTime().ToString("o")
    finished_at = $finishedAt.ToUniversalTime().ToString("o")
    requested_minutes = $Minutes
    max_iterations = $MaxIterations
    elapsed_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
    successful_iterations = $iterations
    failed_iteration = if ($null -eq $failure) { $null } else { $iterations }
    error = if ($null -eq $failure) { $null } else { $failure.Exception.Message }
}
$report | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $artifacts "workflow-soak.json")
if ($null -ne $failure) {
    throw $failure
}
Write-Host "Completed $iterations workflow soak iterations."
