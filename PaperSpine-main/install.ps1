param(
  [ValidateSet("codex", "claude-code", "both")]
  [string]$Target = "codex",
  [switch]$CleanLegacy,
  [switch]$CheckOnly,
  [string]$ProfileRoot = (Join-Path $env:USERPROFILE ".paperspine5\profiles\default"),
  [string]$ManifestPath = "",
  [string]$BundlePath = "",
  [string]$CodexSkillsRoot = "",
  [string]$ClaudeSkillsRoot = "",
  [string]$LegacyArchiveRoot = (Join-Path $env:USERPROFILE ".paperspine5\legacy-migrations")
)
$ErrorActionPreference = "Stop"
$Version = "0.4.0-alpha.1-dev"
$ManifestUrl = "https://github.com/WUBING2023/PaperSpine/releases/download/v$Version/manifest.json"
$MirrorManifestUrl = "https://wubing2023.github.io/PaperSpine/v5/downloads/manifest.json"

# GitHub only speaks TLS 1.2 and newer. Windows PowerShell 5.1 on older Windows
# builds still offers SSL3/TLS1.0 by default, and the handshake then dies with
# SEC_E_NO_CREDENTIALS or "Could not create SSL/TLS secure channel" before any
# HTTP status exists. That reads like a missing file, but it is a local TLS
# fault: the release assets are published and downloadable from a healthy host.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

if ($ManifestPath) {
  $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
  try {
    $manifest = Invoke-RestMethod -Uri $ManifestUrl -UseBasicParsing
  } catch {
    Write-Host ""
    Write-Host "Could not read the release manifest:" -ForegroundColor Red
    Write-Host "  $ManifestUrl"
    Write-Host "  $($_.Exception.Message)"
    Write-Host ""
    Write-Host "This is a network/TLS failure on this machine, not a missing release."
    Write-Host "The v$Version release does publish its assets."
    Write-Host ""
    Write-Host "  1. A proxy may be intercepting TLS:  netsh winhttp show proxy"
    Write-Host "  2. Try the website mirror (different host):"
    Write-Host "       $MirrorManifestUrl"
    Write-Host "  3. Install fully offline from files fetched on another machine."
    Write-Host "     BOTH paths are required - without -ManifestPath the installer"
    Write-Host "     still needs the network:"
    Write-Host "       .\install.ps1 -ManifestPath .\manifest.json -BundlePath .\paperspine5-suite-$Version.zip"
    throw
  }
}
$artifact = @($manifest.artifacts | Where-Object { $_.kind -eq "suite" }) | Select-Object -First 1
if ($null -eq $artifact) { throw "V5 suite artifact is missing from the release manifest." }
$profileStatePath = Join-Path $ProfileRoot ".paperspine5-lifecycle\profile-state.json"
if ($CheckOnly) {
  $currentBuild = $null
  if (Test-Path -LiteralPath $profileStatePath) {
    $profileState = Get-Content -LiteralPath $profileStatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $currentBuild = $profileState.active_build_id
  }
  $status = if (-not $currentBuild) { "not_installed" } elseif ($currentBuild -eq $manifest.build_id) { "up_to_date" } else { "update_available" }
  [ordered]@{ status = $status; current_build_id = $currentBuild; available_build_id = $manifest.build_id; version = $manifest.version; release_url = $manifest.release_url } | ConvertTo-Json
  return
}
$downloadRoot = Join-Path ([IO.Path]::GetTempPath()) ("paperspine5-v5-" + [guid]::NewGuid().ToString("N"))
$extractRoot = Join-Path $downloadRoot "suite"
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
$zipPath = Join-Path $downloadRoot $artifact.file
try {
  if ($BundlePath) { Copy-Item -LiteralPath $BundlePath -Destination $zipPath -Force } else { Invoke-WebRequest -Uri $artifact.download_url -OutFile $zipPath -UseBasicParsing }
  $actualHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($actualHash -ne $artifact.sha256.ToLowerInvariant()) { throw "SHA-256 mismatch for $($artifact.file)." }
  if ((Get-Item -LiteralPath $zipPath).Length -ne [int64]$artifact.bytes) { throw "Byte-size mismatch for $($artifact.file)." }
  Expand-Archive -LiteralPath $zipPath -DestinationPath $extractRoot -Force
  $launcher = Join-Path $extractRoot "paperspine.cmd"
  $runtime = Join-Path $extractRoot "runtime_vendor\windows-py312\python.exe"
  $migrator = Join-Path $extractRoot "standalone\paper-spine\scripts\skill_discovery_migration.py"
  if (-not (Test-Path -LiteralPath $launcher) -or -not (Test-Path -LiteralPath $runtime)) { throw "The downloaded V5 suite is incomplete." }
  & $launcher verify-bundle --bundle $zipPath | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "The downloaded V5 suite failed its internal verification." }
  $targets = if ($Target -eq "both") { @("codex", "claude-code") } else { @($Target) }
  $roots = @{}
  foreach ($name in $targets) {
    $roots[$name] = if ($name -eq "codex") { if ($CodexSkillsRoot) { $CodexSkillsRoot } else { Join-Path $env:USERPROFILE ".codex\skills" } } else { if ($ClaudeSkillsRoot) { $ClaudeSkillsRoot } else { Join-Path $env:USERPROFILE ".claude\skills" } }
  }
  $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $backupRoot = Join-Path $env:USERPROFILE ".paperspine5\backups\v5-install\$stamp"
  $operationId = if (Test-Path -LiteralPath $profileStatePath) { "v5-update-$stamp" } else { "v5-install-$stamp" }
  if ($CleanLegacy) {
    $migrationArgs = @("-I", "-B", $migrator, "migrate", "--archive-root", $LegacyArchiveRoot, "--operation-id", $operationId)
    foreach ($entry in $roots.GetEnumerator()) { $migrationArgs += @("--skills-root", "$($entry.Key)=$($entry.Value)") }
    & $runtime @migrationArgs | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Known legacy Skill migration did not complete; no V5 Skill was activated." }
  }
  $skillSource = Join-Path $extractRoot "standalone\paper-spine"
  foreach ($entry in $roots.GetEnumerator()) {
    $hostRoot = $entry.Value; $destination = Join-Path $hostRoot "paper-spine"
    New-Item -ItemType Directory -Path $hostRoot -Force | Out-Null
    if (Test-Path -LiteralPath $destination) {
      $targetBackup = Join-Path $backupRoot (Join-Path $entry.Key "paper-spine")
      New-Item -ItemType Directory -Path (Split-Path $targetBackup) -Force | Out-Null
      Move-Item -LiteralPath $destination -Destination $targetBackup
    }
    Copy-Item -LiteralPath $skillSource -Destination $destination -Recurse -Force
    Write-Output "Installed V5 paper-spine Skill for $($entry.Key): $destination"
  }
  New-Item -ItemType Directory -Path $ProfileRoot -Force | Out-Null
  if (Test-Path -LiteralPath $profileStatePath) {
    & $launcher update --profile-root $ProfileRoot --bundle $zipPath --operation-id $operationId --confirm | Out-Host
  } else {
    & $launcher install --profile-root $ProfileRoot --bundle $zipPath --operation-id $operationId | Out-Host
  }
  if ($LASTEXITCODE -ne 0) { throw "V5 profile installation/update failed." }
  & $launcher first-start --profile-root $ProfileRoot | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "V5 first-start health check failed." }
  Write-Output "PaperSpine5 V5 installed. Start a new host session before invoking paper-spine."
  Write-Output "Profile: $ProfileRoot"
  if ($CleanLegacy) { Write-Output "Known V3/V4 discovery folders were archived; user task data was retained." }
  Write-Output "Backup root: $backupRoot"
} finally {
  if (Test-Path -LiteralPath $downloadRoot) { Remove-Item -LiteralPath $downloadRoot -Recurse -Force }
}
