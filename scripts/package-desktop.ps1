param(
  [switch]$SkipFrontendBuild,
  [switch]$AllowDirty,
  [switch]$AllowUntagged
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $root
$outputs = Join-Path $workspace "outputs"
$stage = Join-Path $root "work\release-package"
$version = (Get-Content -LiteralPath (Join-Path $root "VERSION") -Raw).Trim()
$portableName = "NovelAgentStudio-Portable-$version.zip"
$setupName = "NovelAgentStudio-Setup-$version.exe"

function Assert-ChildPath {
  param([Parameter(Mandatory=$true)][string]$Path, [Parameter(Mandatory=$true)][string]$Parent)
  $resolvedPath = [System.IO.Path]::GetFullPath($Path)
  $resolvedParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
  if (-not $resolvedPath.StartsWith($resolvedParent, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to modify a path outside $resolvedParent`: $resolvedPath"
  }
  return $resolvedPath
}

function Invoke-Checked {
  param([Parameter(Mandatory=$true)][scriptblock]$Command, [Parameter(Mandatory=$true)][string]$Failure)
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Failure (exit code $LASTEXITCODE)"
  }
}

$resolvedStage = Assert-ChildPath -Path $stage -Parent $root
$resolvedOutputs = Assert-ChildPath -Path $outputs -Parent $workspace
New-Item -ItemType Directory -Force -Path $resolvedOutputs | Out-Null
if (Test-Path -LiteralPath $resolvedStage) {
  Remove-Item -LiteralPath $resolvedStage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $resolvedStage | Out-Null

$python = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  throw "Missing backend virtual environment: $python"
}
$releaseMetadata = Join-Path $root "scripts\release_metadata.py"
Invoke-Checked -Command { & $python $releaseMetadata verify } -Failure "Version metadata is inconsistent"
$buildProvenance = Join-Path $resolvedStage "build-provenance.json"
$manifestArgs = @($releaseMetadata, "manifest", "--output", $buildProvenance)
if ($AllowDirty) { $manifestArgs += "--allow-dirty" }
if ($AllowUntagged) { $manifestArgs += "--allow-untagged" }
Invoke-Checked -Command { & $python @manifestArgs } -Failure "Release provenance check failed"
$frontendHash = (& $python $releaseMetadata frontend-hash).Trim()
if ($LASTEXITCODE -ne 0 -or -not $frontendHash) {
  throw "Could not calculate the frontend source hash"
}
$frontendStamp = Join-Path $root "frontend\dist\build-provenance.json"
if ($SkipFrontendBuild -and -not $AllowDirty -and -not $AllowUntagged) {
  throw "Tagged release builds must rebuild the frontend"
}
if (-not $SkipFrontendBuild) {
  Push-Location (Join-Path $root "frontend")
  try {
    Invoke-Checked -Command { pnpm.cmd run build } -Failure "Frontend production build failed"
  } finally {
    Pop-Location
  }
  $frontendBuildInfo = [ordered]@{
    schema_version = 1
    version = $version
    frontend_source_sha256 = $frontendHash
  } | ConvertTo-Json
  Set-Content -LiteralPath $frontendStamp -Value $frontendBuildInfo -Encoding UTF8
} else {
  if (-not (Test-Path -LiteralPath $frontendStamp)) {
    throw "SkipFrontendBuild requires frontend/dist/build-provenance.json"
  }
  $frontendBuildInfo = Get-Content -LiteralPath $frontendStamp -Raw | ConvertFrom-Json
  if ($frontendBuildInfo.version -ne $version -or $frontendBuildInfo.frontend_source_sha256 -ne $frontendHash) {
    throw "Frontend dist is stale for the current version or source tree"
  }
}
if (-not (Test-Path -LiteralPath (Join-Path $root "frontend\dist\index.html"))) {
  throw "Frontend dist is missing"
}

$pyinstallerDist = Join-Path $resolvedStage "pyinstaller-dist"
$pyinstallerWork = Join-Path $resolvedStage "pyinstaller-work"
Push-Location $root
try {
  Invoke-Checked -Command {
    & $python -m PyInstaller --noconfirm --clean --distpath $pyinstallerDist --workpath $pyinstallerWork (Join-Path $root "NovelAgentStudio.spec")
  } -Failure "PyInstaller build failed"
} finally {
  Pop-Location
}

$appSource = Join-Path $pyinstallerDist "NovelAgentStudio"
foreach ($required in @("NovelAgentStudio.exe", "NovelAgentStudioConsole.exe", "_internal\frontend-dist\index.html", "_internal\frontend-dist\build-provenance.json", "_internal\alembic.ini")) {
  if (-not (Test-Path -LiteralPath (Join-Path $appSource $required))) {
    throw "Packaged application is incomplete: $required"
  }
}
Copy-Item -LiteralPath $buildProvenance -Destination (Join-Path $appSource "build-provenance.json") -Force
Set-Content -LiteralPath (Join-Path $appSource "VERSION") -Value $version -Encoding ASCII -NoNewline

$cscCandidates = @(
  "$env:SystemRoot\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
  "$env:SystemRoot\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $csc) {
  throw "Cannot find the .NET Framework C# compiler"
}

$uninstaller = Join-Path $appSource "Uninstall.exe"
Invoke-Checked -Command {
  & $csc /nologo /target:winexe /optimize+ /out:$uninstaller /reference:System.Windows.Forms.dll (Join-Path $root "scripts\NovelAgentStudioUninstaller.cs")
} -Failure "Uninstaller compilation failed"

$readme = @(
  "Novel Agent Studio"
  "Version: $version"
  ""
  "Start: double-click NovelAgentStudio.exe"
  "Diagnostics: run NovelAgentStudioConsole.exe --smoke-test"
  ""
  "Installed data: %LOCALAPPDATA%\NovelAgentStudioV2\data"
  "Portable data: the data folder beside this application"
  ""
  "The application opens in its own desktop window and sends no telemetry."
  "Close the window to continue in the system tray or stop and exit."
) -join [Environment]::NewLine
Set-Content -LiteralPath (Join-Path $appSource "README.txt") -Value $readme -Encoding UTF8

$smokeData = Join-Path $resolvedStage "smoke-data"
Invoke-Checked -Command {
  & (Join-Path $appSource "NovelAgentStudioConsole.exe") --smoke-test --data-dir $smokeData
} -Failure "Packaged application smoke test failed"

$guiSmokeData = Join-Path $resolvedStage "gui-smoke-data"
Invoke-Checked -Command {
  & (Join-Path $appSource "NovelAgentStudioConsole.exe") --gui-smoke-test-seconds 10 --data-dir $guiSmokeData
} -Failure "Packaged GUI lifecycle smoke test failed"

$portableParent = Join-Path $resolvedStage "portable"
$portableRoot = Join-Path $portableParent "NovelAgentStudio"
New-Item -ItemType Directory -Force -Path $portableParent | Out-Null
Copy-Item -LiteralPath $appSource -Destination $portableRoot -Recurse -Force
New-Item -ItemType File -Force -Path (Join-Path $portableRoot "portable.flag") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $portableRoot "data") | Out-Null

$portableZip = Join-Path $resolvedOutputs $portableName
$setupExe = Join-Path $resolvedOutputs $setupName
foreach ($old in Get-ChildItem -LiteralPath $resolvedOutputs -File | Where-Object {
  $_.Name -eq $portableName -or
  $_.Name -eq $setupName -or
  $_.Name -eq "SHA256SUMS.txt"
}) {
  Remove-Item -LiteralPath $old.FullName -Force
}
Compress-Archive -LiteralPath $portableRoot -DestinationPath $portableZip -CompressionLevel Optimal

$portableVerifyParent = Join-Path $resolvedStage "portable-verify"
Expand-Archive -LiteralPath $portableZip -DestinationPath $portableVerifyParent
$portableVerifyRoot = Join-Path $portableVerifyParent "NovelAgentStudio"
foreach ($required in @("NovelAgentStudio.exe", "NovelAgentStudioConsole.exe", "build-provenance.json", "VERSION", "portable.flag")) {
  if (-not (Test-Path -LiteralPath (Join-Path $portableVerifyRoot $required))) {
    throw "Portable ZIP is incomplete after extraction: $required"
  }
}
Invoke-Checked -Command {
  & (Join-Path $portableVerifyRoot "NovelAgentStudioConsole.exe") --smoke-test --data-dir (Join-Path $resolvedStage "portable-verify-data")
} -Failure "Extracted portable ZIP smoke test failed"
Invoke-Checked -Command {
  & (Join-Path $portableVerifyRoot "NovelAgentStudioConsole.exe") --gui-smoke-test-seconds 10 --data-dir (Join-Path $resolvedStage "portable-verify-gui-data")
} -Failure "Extracted portable ZIP GUI lifecycle smoke test failed"

$payloadZip = Join-Path $resolvedStage "installer-payload.zip"
Compress-Archive -Path (Join-Path $appSource "*") -DestinationPath $payloadZip -CompressionLevel Optimal
$payloadHashFile = Join-Path $resolvedStage "payload.sha256"
$payloadHash = (Get-FileHash -LiteralPath $payloadZip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $payloadHashFile -Value $payloadHash -Encoding ASCII -NoNewline

$payloadResource = "/resource:$payloadZip,payload.zip"
$hashResource = "/resource:$payloadHashFile,payload.sha256"
Invoke-Checked -Command {
  & $csc /nologo /target:winexe /optimize+ /out:$setupExe $payloadResource $hashResource /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll (Join-Path $root "scripts\NovelAgentStudioInstaller.cs")
} -Failure "Installer compilation failed"

foreach ($artifact in @($portableZip, $setupExe)) {
  if (-not (Test-Path -LiteralPath $artifact) -or (Get-Item -LiteralPath $artifact).Length -lt 1MB) {
    throw "Release artifact is missing or unexpectedly small: $artifact"
  }
}

$checksums = @(
  ("{0}  {1}" -f (Get-FileHash -LiteralPath $setupExe -Algorithm SHA256).Hash.ToLowerInvariant(), (Split-Path -Leaf $setupExe))
  ("{0}  {1}" -f (Get-FileHash -LiteralPath $portableZip -Algorithm SHA256).Hash.ToLowerInvariant(), (Split-Path -Leaf $portableZip))
)
$checksumPath = Join-Path $resolvedOutputs "SHA256SUMS.txt"
Set-Content -LiteralPath $checksumPath -Value $checksums -Encoding ASCII

Write-Host "Packaged application smoke test: PASS"
Write-Host "Packaged GUI lifecycle smoke test: PASS"
Write-Host "Extracted portable ZIP smoke test: PASS"
Write-Host "Extracted portable ZIP GUI lifecycle smoke test: PASS"
Write-Host "Portable ZIP: $portableZip"
Write-Host "Installer EXE: $setupExe"
Write-Host "Checksums: $checksumPath"
