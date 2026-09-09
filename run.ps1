# One command to run everything on Windows: backend, frontend, database, browser.
#
# PowerShell equivalent of run.sh. Idempotent -- it only does setup work that is
# missing, so the second run starts in a couple of seconds.
#
#   powershell -ExecutionPolicy Bypass -File .\run.ps1
#
# The ExecutionPolicy flag is usually needed: Windows blocks unsigned scripts by
# default, and that block is silent enough to look like nothing happened.

# Applies to cmdlets. Native commands are checked by exit code instead, via
# Invoke-Native below -- with Stop in force, any tool that merely writes a
# warning to stderr would abort the script.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Invoke-Native {
    <#
      Run an external command, tolerating stderr output, and fail only on a
      non-zero exit code. Returns the exit code when -AllowFailure is set.
    #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [switch]$AllowFailure,
        [switch]$Quiet
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) {
            & $Exe @Arguments *> $null
        } else {
            & $Exe @Arguments 2>&1 | ForEach-Object { "$_" }
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }

    if ($code -ne 0 -and -not $AllowFailure) {
        throw "$Exe exited with code $code"
    }
    return $code
}

$Venv = Join-Path $PSScriptRoot ".venv"
$Scripts = Join-Path $Venv "Scripts"          # Windows venv layout, not bin/
$Py = Join-Path $Scripts "python.exe"
$Pip = Join-Path $Scripts "pip.exe"

# --- config -----------------------------------------------------------------
if (-not (Test-Path ".env")) {
    Write-Host "==> no .env found, copying .env.example"
    Copy-Item ".env.example" ".env"
    Write-Host "    edit .env to add your NVIDIA_API_KEY (free from build.nvidia.com)"
}

function Get-EnvValue([string]$Key, [string]$Default) {
    $line = Select-String -Path ".env" -Pattern "^$Key=" -ErrorAction SilentlyContinue |
            Select-Object -Last 1
    if (-not $line) { return $Default }
    $value = ($line.Line -split "=", 2)[1].Trim()
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

$BackendHost = Get-EnvValue "BACKEND_HOST" "127.0.0.1"
$BackendPort = Get-EnvValue "BACKEND_PORT" "8000"
$FrontendPort = Get-EnvValue "FRONTEND_PORT" "5173"
$OpenBrowser = Get-EnvValue "OPEN_BROWSER" "true"

# --- prerequisites ----------------------------------------------------------
function Find-Python {
    foreach ($candidate in @("py", "python", "python3")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

# Report everything that is missing at once. Finding out about one prerequisite,
# installing it, and then finding out about the next is a bad first experience.
$missing = @()
if (-not (Find-Python)) {
    $missing += "  Python 3.11+   winget install Python.Python.3.12    (or python.org)"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $missing += "  Node.js LTS    winget install OpenJS.NodeJS.LTS     (or nodejs.org)"
}

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing prerequisites:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host $_ }
    Write-Host ""
    Write-Host "Install them, then CLOSE AND REOPEN this terminal so PATH refreshes."
    Write-Host "PyCharm caches PATH per terminal tab, so a new tab is not always enough --"
    Write-Host "restart PyCharm if 'node --version' still fails after installing."
    Write-Host ""
    Write-Host "Optional, for PDF output: MiKTeX from miktex.org. Without it the app"
    Write-Host "still runs and produces a tailored .tex, and says so in the health check."
    exit 1
}

# --- setup ------------------------------------------------------------------
$FirstRun = $false

if (-not (Test-Path $Py)) {
    $FirstRun = $true
    Write-Host "==> first run: setting up. This takes a couple of minutes."
    Write-Host "    creating virtualenv"
    Invoke-Native -Exe (Find-Python) -Arguments @("-m", "venv", $Venv) | Out-Null
    # Not $Pip: pip.exe cannot replace itself on Windows while it is running.
    Invoke-Native -Exe $Py -Arguments @("-m", "pip", "install", "--upgrade", "pip") -Quiet -AllowFailure | Out-Null
}

# Expected to fail before the first install; that is what it is asking.
$installed = Invoke-Native -Exe $Py -Arguments @("-c", "import aicvtailor") -Quiet -AllowFailure
if ($installed -ne 0) {
    $FirstRun = $true
    Write-Host "    installing Python dependencies (this is the slow one)"
    Invoke-Native -Exe $Py -Arguments @("-m", "pip", "install", "-e", ".[dev]") | Out-Null
}

if (-not (Test-Path "frontend\node_modules")) {
    $FirstRun = $true
    Write-Host "    installing frontend dependencies"
    Push-Location frontend
    try {
        Invoke-Native -Exe "cmd.exe" -Arguments @("/c", "npm install --no-fund --no-audit") | Out-Null
    } finally { Pop-Location }
}

if ($FirstRun) { Write-Host "==> setup done" }

Write-Host "==> preparing database"
Invoke-Native -Exe (Join-Path $Scripts "aicvtailor.exe") -Arguments @("init-db") | Out-Null

Write-Host "==> component check"
# Exits non-zero when no provider is usable yet, which is a finding, not a fault.
Invoke-Native -Exe (Join-Path $Scripts "aicvtailor.exe") -Arguments @("doctor") -AllowFailure | Out-Null

# --- ports ------------------------------------------------------------------
function Test-PortInUse([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

foreach ($pair in @(@($BackendPort, "backend"), @($FrontendPort, "frontend"))) {
    if (Test-PortInUse ([int]$pair[0])) {
        Write-Error "Port $($pair[0]) ($($pair[1])) is already in use. An earlier run may still be going."
        exit 1
    }
}

# --- run --------------------------------------------------------------------
$Started = @()

try {
    Write-Host "==> backend  http://${BackendHost}:${BackendPort}"
    $Started += Start-Process -FilePath (Join-Path $Scripts "uvicorn.exe") `
        -ArgumentList "aicvtailor.main:app", "--host", $BackendHost, "--port", $BackendPort,
                      "--reload", "--reload-dir", "backend/src" `
        -NoNewWindow -PassThru

    Write-Host "==> frontend http://localhost:$FrontendPort"
    $env:BACKEND_PORT = $BackendPort
    $env:FRONTEND_PORT = $FrontendPort
    $Started += Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "npm run dev" `
        -WorkingDirectory (Join-Path $PSScriptRoot "frontend") -NoNewWindow -PassThru

    if ($OpenBrowser -eq "true") {
        foreach ($attempt in 1..40) {
            if (Test-PortInUse ([int]$FrontendPort)) {
                Start-Process "http://localhost:$FrontendPort"
                break
            }
            Start-Sleep -Milliseconds 250
        }
    }

    Write-Host "==> running. Ctrl-C stops both."
    while ($true) {
        Start-Sleep -Seconds 1
        if ($Started | Where-Object { $_.HasExited }) {
            Write-Host "==> a server exited; shutting down"
            break
        }
    }
} finally {
    # npm and uvicorn --reload both spawn grandchildren, so stopping the process
    # we launched is not enough. taskkill /T walks the tree.
    foreach ($proc in $Started) {
        if ($proc -and -not $proc.HasExited) {
            & taskkill /PID $proc.Id /T /F 2>$null | Out-Null
        }
    }
}
