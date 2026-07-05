$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$dashboardUrl = "http://localhost:18021"
$healthUrl = "$dashboardUrl/health"

Set-Location -LiteralPath $projectDir

Write-Host "Project directory: $projectDir"
Write-Host "Starting Cattea Memory New..."
docker compose up -d

Write-Host "Health check: $healthUrl"
$healthy = $false

for ($i = 1; $i -le 30; $i++) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            $healthy = $true
            Write-Host "Health OK: HTTP $($response.StatusCode)"
            break
        }

        Write-Host "Health check attempt $i returned HTTP $($response.StatusCode). Waiting..."
    }
    catch {
        Write-Host "Health check attempt $i is not ready yet. Waiting..."
    }

    Start-Sleep -Seconds 2
}

if (-not $healthy) {
    Write-Warning "Start command completed, but health check did not pass yet. Check again later: $healthUrl"
    exit 1
}

Write-Host ""
Write-Host "Dashboard: $dashboardUrl"
