param(
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA "Programs\Inception\bin"),
    [switch]$SkipPreflight,
    [switch]$SkipPathUpdate
)

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-WorkingCommand {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $false
    }
    try {
        & $command.Source --version *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Preserve-And-Write {
    param(
        [string]$Path,
        [string]$Content
    )
    if (Test-Path -LiteralPath $Path) {
        $existing = Get-Content -LiteralPath $Path -Raw
        if ($existing -eq $Content) {
            return
        }
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $backup = "$Path.backup-$stamp"
        $suffix = 1
        while (Test-Path -LiteralPath $backup) {
            $backup = "$Path.backup-$stamp-$suffix"
            $suffix += 1
        }
        Move-Item -LiteralPath $Path -Destination $backup
        Write-Host "Preserved previous launcher: $backup"
    }
    Set-Content -LiteralPath $Path -Value $Content -Encoding Ascii -NoNewline
}

$required = @(
    "scripts\inception.py",
    "scripts\cockpit.py",
    "scripts\operating_room.py",
    "scripts\capture_browser.cjs",
    "scripts\point_browser.cjs",
    "scripts\capture_windows_screen.ps1",
    "scripts\listen_windows.ps1",
    "context\WORKING_COVENANT.md",
    "context\MICROHISTORY_V1.md"
)
foreach ($relative in $required) {
    $path = Join-Path $RepoDir $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Downloaded repository is incomplete: missing $relative"
    }
}

$pythonPath = $null
$pythonCandidates = @()
foreach ($name in @("python", "python3")) {
    try {
        $pythonCandidates += @(& where.exe $name 2> $null)
    }
    catch {
        # Windows Store command stubs often exist but cannot actually run.
    }
}
foreach ($candidate in @($pythonCandidates | Select-Object -Unique)) {
    try {
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $pythonPath = $candidate
            break
        }
    }
    catch {
        continue
    }
}
if ($null -eq $pythonPath) {
    throw "Working Python 3 is required. Install it, then run install.ps1 again."
}

if (-not $SkipPreflight) {
    $providers = @()
    if (Test-WorkingCommand "claude") { $providers += "Claude" }
    if (Test-WorkingCommand "codex") { $providers += "Codex" }
    if (Test-WorkingCommand "agy") {
        $providers += "Antigravity"
    }
    elseif (Test-WorkingCommand "gemini") {
        $providers += "Gemini"
    }
    if ($providers.Count -lt 2) {
        $found = if ($providers.Count) { $providers -join ", " } else { "none" }
        throw "Inception needs any two installed model commands. Found: $found"
    }
    if (-not (Test-WorkingCommand "magick")) {
        throw "ImageMagick is required for screenshot pointing."
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        throw "Git for Windows is required."
    }
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$pythonPath = $pythonPath.Replace('"', '""')
$scriptPath = (Join-Path $RepoDir "scripts\inception.py").Replace('"', '""')
$launcher = "@echo off`r`n`"$pythonPath`" `"$scriptPath`" %*`r`n"
Preserve-And-Write -Path (Join-Path $BinDir "inception.cmd") -Content $launcher

if (-not $SkipPathUpdate) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @($userPath -split ";" | Where-Object { $_ })
    if ($entries -notcontains $BinDir) {
        $newPath = (($entries + $BinDir) -join ";")
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        $env:Path = "$env:Path;$BinDir"
        Write-Host "Added Inception to your user PATH."
    }
}

Write-Host ""
Write-Host "Inception is installed for PowerShell."
Write-Host "Open a new PowerShell window, then run:"
Write-Host "  inception cockpit"
Write-Host ""
Write-Host "Use any two signed-in commands: Claude, Codex, or Antigravity (agy)."
Write-Host "Codex is optional. Run each chosen model command once to finish its sign-in."
