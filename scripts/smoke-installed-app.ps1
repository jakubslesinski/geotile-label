param(
    [Parameter()]
    [string]$ExePath = "",

    [Parameter()]
    [int]$StartupSeconds = 60
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ExePath)) {
    $candidates = @(
        "$env:LOCALAPPDATA\GeoTile Label\geotile-label-desktop.exe",
        "C:\Program Files\GeoTile Label\geotile-label-desktop.exe"
    )
    $ExePath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if ([string]::IsNullOrWhiteSpace($ExePath) -or -not (Test-Path $ExePath)) {
    throw "GeoTile Label executable not found. Pass -ExePath explicitly."
}

$logsDir = Join-Path $env:APPDATA "GeoTileLabel\logs"
$process = Start-Process -FilePath $ExePath -PassThru
Start-Sleep -Seconds $StartupSeconds

if ($process.HasExited) {
    $diagnosticsZip = Join-Path $env:TEMP ("GeoTileLabel-installed-smoke-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".zip")
    if (Test-Path $logsDir) {
        Compress-Archive -Path (Join-Path $logsDir "*") -DestinationPath $diagnosticsZip -Force
    }
    throw "GeoTile Label exited during startup. ExitCode=$($process.ExitCode). Diagnostics: $diagnosticsZip"
}

Stop-Process -Id $process.Id -Force
Write-Host "Installed app smoke test passed. Process stayed alive for $StartupSeconds seconds."
