<#
.SYNOPSIS
    DVD Server installer for Windows.

.DESCRIPTION
    Creates a virtualenv, installs Python dependencies, and (optionally)
    registers a Scheduled Task that starts the server at user logon.

    Run with Administrator for the scheduled task to be registered:
        powershell -ExecutionPolicy Bypass -File .\install.ps1

    Without Admin:
        powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoService

.PARAMETER Port
    Listening port. Default 4251.

.PARAMETER ServiceName
    Scheduled Task name. Default "DvdServer".

.PARAMETER InstallDir
    Override the install directory. Defaults to this script's directory.

.PARAMETER NoService
    Skip scheduled-task registration; just set up venv + deps.

.PARAMETER NoFfmpegCheck
    Skip the "is ffmpeg on PATH?" warning.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\install.ps1
#>
[CmdletBinding()]
param(
    [int]    $Port          = 4251,
    [string] $ServiceName   = "DvdServer",
    [string] $InstallDir    = "",
    [switch] $NoService,
    [switch] $NoFfmpegCheck
)

$ErrorActionPreference = "Stop"

function Write-Info  { param($m) Write-Host $m -ForegroundColor Cyan }
function Write-Ok    { param($m) Write-Host $m -ForegroundColor Green }
function Write-Warn2 { param($m) Write-Host "WARNING: $m" -ForegroundColor Yellow }
function Fail        { param($m) Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# ---- admin check ----
$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $NoService -and -not $isAdmin) {
    Fail "Registering a Scheduled Task requires Administrator. Re-run as Admin, or pass -NoService."
}

# ---- resolve install dir ----
if (-not $InstallDir) {
    $InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
if (-not (Test-Path -LiteralPath $InstallDir)) {
    Fail "Install directory does not exist: $InstallDir"
}
$InstallDir = (Resolve-Path -LiteralPath $InstallDir).Path
$backendPath = Join-Path $InstallDir "dvd_server_backend.py"
if (-not (Test-Path -LiteralPath $backendPath)) {
    Fail "dvd_server_backend.py not found in $InstallDir"
}

$venvDir = Join-Path $InstallDir "venv"
$pyExe   = Join-Path $venvDir   "Scripts\python.exe"
$pipExe  = Join-Path $venvDir   "Scripts\pip.exe"

Write-Info "Installing DVD Server"
Write-Host "  User:        $env:USERNAME"
Write-Host "  Install dir: $InstallDir"
Write-Host "  Venv:        $venvDir"
Write-Host "  Port:        $Port"
Write-Host "  Task name:   $ServiceName"
Write-Host ""

# ---- find python ----
Write-Info "==> Checking Python"
$pyLauncher = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pyLauncher = $candidate
        break
    }
}
if (-not $pyLauncher) {
    Fail ("Python not found on PATH.`n" +
          "Install from https://www.python.org/downloads/windows/ and tick " +
          "'Add Python to PATH'.")
}
Write-Host "  Found: $(& $pyLauncher --version 2>&1)"

# ---- ffmpeg check ----
if (-not $NoFfmpegCheck) {
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if (-not $ff) {
        Write-Warn2 "ffmpeg not found on PATH."
        Write-Warn2 "Audio-track switching and embedded subtitle extraction will be disabled."
        Write-Warn2 ""
        Write-Warn2 "  Easiest install:   winget install Gyan.FFmpeg"
        Write-Warn2 "  Or download from:  https://www.gyan.dev/ffmpeg/builds/"
        Write-Warn2 "  Full instructions: $InstallDir\EXTERNAL_PROGRAMS.md"
        Write-Warn2 ""
    } else {
        Write-Host "  ffmpeg: $($ff.Source)"
    }
}

# ---- venv ----
Write-Info "==> Creating virtualenv"
if (-not (Test-Path -LiteralPath $pyExe)) {
    if ($pyLauncher -eq "py") {
        & py -3 -m venv $venvDir
    } else {
        & $pyLauncher -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed." }
} else {
    Write-Host "  Already exists; reusing."
}

# ---- packages ----
Write-Info "==> Installing Python packages"
$reqPath = Join-Path $InstallDir "requirements.txt"

& $pipExe install --upgrade pip wheel
if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed." }

if (Test-Path -LiteralPath $reqPath) {
    & $pipExe install -r $reqPath
} else {
    Write-Warn2 "requirements.txt not found — installing defaults inline."
    & $pipExe install flask flask-cors waitress zeroconf
}
if ($LASTEXITCODE -ne 0) { Fail "package install failed." }

# ---- run.cmd launcher ----
$runCmdPath = Join-Path $InstallDir "run.cmd"
$runCmdBody = @"
@echo off
cd /d "%~dp0"
"$pyExe" "$backendPath"
echo.
echo --- server exited (press any key to close) ---
pause >nul
"@
Set-Content -LiteralPath $runCmdPath -Value $runCmdBody -Encoding ASCII
Write-Host "  Launcher:    $runCmdPath"

# ---- scheduled task ----
if (-not $NoService) {
    Write-Info "==> Registering Scheduled Task '$ServiceName'"

    $existing = Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false
        Write-Host "  Removed existing task."
    }

    $action = New-ScheduledTaskAction `
        -Execute $pyExe `
        -Argument "`"$backendPath`"" `
        -WorkingDirectory $InstallDir

    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -RestartCount 3 `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew

    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Limited

    Register-ScheduledTask `
        -TaskName   $ServiceName `
        -Action     $action `
        -Trigger    $trigger `
        -Settings   $settings `
        -Principal  $principal `
        -Description "DVD MKV Server — runs at user logon." | Out-Null

    Write-Host "  Registered. Starting it now..."
    Stop-ScheduledTask  -TaskName $ServiceName -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $ServiceName
    Start-Sleep -Seconds 2

    $state = (Get-ScheduledTask -TaskName $ServiceName).State
    Write-Host "  Task state: $state"
}

# ---- summary ----
$ip = $null
try {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
           Where-Object { $_.PrefixOrigin -ne "WellKnown" -and
                          $_.IPAddress    -notlike "169.254.*" -and
                          $_.IPAddress    -notlike "127.*" } |
           Sort-Object InterfaceMetric |
           Select-Object -First 1).IPAddress
} catch { }
if (-not $ip) { $ip = "localhost" }

Write-Host ""
Write-Ok "Done."
Write-Host "  URL:       http://${ip}:$Port"
if (-not $NoService) {
    Write-Host "  Status:    Get-ScheduledTask -TaskName $ServiceName | Select State"
    Write-Host "  Stop:      Stop-ScheduledTask  -TaskName $ServiceName"
    Write-Host "  Start:     Start-ScheduledTask -TaskName $ServiceName"
    Write-Host "  Remove:    Unregister-ScheduledTask -TaskName $ServiceName -Confirm:`$false"
}
Write-Host "  Manual:    $runCmdPath"
Write-Host ""
Write-Host "Drop DVD folders into:"
Write-Host "    $InstallDir\dvds\<Genre>\<DVD Folder>\*.mkv"
Write-Host "    $InstallDir\dvds\<DVD Folder>\*.mkv       # appears as Uncategorized"
Write-Host ""
Write-Host "Documentation:"
Write-Host "    External programs + downloads: $InstallDir\EXTERNAL_PROGRAMS.md"
Write-Host "    Python dependencies:           $InstallDir\requirements.txt"
Write-Host ""
Write-Host "To watch live logs, stop the task and run the launcher manually."
