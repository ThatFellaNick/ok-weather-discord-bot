$ErrorActionPreference = "Stop"

$distro = "Ubuntu-24.04"
$projectDir = "/mnt/e/GitHub/ok-weather-discord-bot"

function Invoke-Wsl {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Command
    )

    & wsl.exe -d $distro -u root -- bash -lc $Command
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code $LASTEXITCODE`: $Command"
    }
}

# Keep the WSL VM alive so Docker containers continue running after startup.
$keepAlive = & wsl.exe -d $distro -u root -- bash -lc "pgrep -af '[s]leep infinity' || true"
if (-not $keepAlive) {
    Start-Process -FilePath "wsl.exe" `
        -ArgumentList @("-d", $distro, "-u", "root", "--exec", "sleep", "infinity") `
        -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

Invoke-Wsl "systemctl start docker"
Invoke-Wsl "cd '$projectDir' && docker compose up -d"
