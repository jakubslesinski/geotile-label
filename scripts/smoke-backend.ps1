param(
    [Parameter(Mandatory = $true)]
    [string]$Config,

    [Parameter()]
    [string]$RepoRoot = "",

    [Parameter()]
    [string]$BackendPython = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

if ([string]::IsNullOrWhiteSpace($BackendPython)) {
    $venvPython = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"
    $BackendPython = if (Test-Path $venvPython) { $venvPython } else { "python" }
}

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse("127.0.0.1"), 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

function Invoke-Api {
    param(
        [string]$Method,
        [string]$Path,
        $Body = $null,
        [string]$OutFile = ""
    )
    $headers = @{ "X-GeoTile-Token" = $script:Token }
    $uri = "$script:BaseUrl/api$Path"
    if ($OutFile) {
        return Invoke-WebRequest -Method $Method -Uri $uri -Headers $headers -OutFile $OutFile -TimeoutSec 120
    }
    if ($null -ne $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -Body ($Body | ConvertTo-Json -Depth 20) -ContentType "application/json" -TimeoutSec 120
    }
    return Invoke-RestMethod -Method $Method -Uri $uri -Headers $headers -TimeoutSec 120
}

function Get-TileXY {
    param([double]$Lon, [double]$Lat, [int]$Zoom)
    $latRad = $Lat * [Math]::PI / 180.0
    $n = [Math]::Pow(2, $Zoom)
    $x = [Math]::Floor(($Lon + 180.0) / 360.0 * $n)
    $y = [Math]::Floor((1.0 - [Math]::Log([Math]::Tan($latRad) + 1.0 / [Math]::Cos($latRad)) / [Math]::PI) / 2.0 * $n)
    return @{ x = [int]$x; y = [int]$y }
}

function Assert-ImageNotBinary {
    param([string]$Path)
    Add-Type -AssemblyName System.Drawing
    $bitmap = [System.Drawing.Bitmap]::new($Path)
    try {
        $colors = @{}
        $stepX = [Math]::Max(1, [Math]::Floor($bitmap.Width / 24))
        $stepY = [Math]::Max(1, [Math]::Floor($bitmap.Height / 24))
        for ($x = 0; $x -lt $bitmap.Width; $x += $stepX) {
            for ($y = 0; $y -lt $bitmap.Height; $y += $stepY) {
                $color = $bitmap.GetPixel($x, $y)
                $key = "$($color.R),$($color.G),$($color.B)"
                $colors[$key] = $true
            }
        }
        if ($colors.Count -le 2) {
            throw "SAR/display tile looks binary or empty: only $($colors.Count) sampled colors"
        }
    } finally {
        $bitmap.Dispose()
    }
}

$configPath = Resolve-Path $Config
$cfg = Get-Content $configPath -Raw | ConvertFrom-Json
$required = @("eo_no_geo", "eo_geo", "sar_16bit_geo", "classes_json")
foreach ($key in $required) {
    if (-not $cfg.$key) {
        throw "Missing config key: $key"
    }
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("geotile-smoke-" + [guid]::NewGuid().ToString("N"))
$dataDir = Join-Path $tempRoot "data"
$logDir = Join-Path $tempRoot "logs"
New-Item -ItemType Directory -Path $dataDir, $logDir -Force | Out-Null

$script:Token = [guid]::NewGuid().ToString()
$port = Get-FreePort
$script:BaseUrl = "http://127.0.0.1:$port"
$backendDir = Join-Path $RepoRoot "backend"

$env:DATA_DIR = $dataDir
$env:GEOTILE_DESKTOP = "1"
$env:GEOTILE_ENABLE_YOLO = "0"
$env:GEOTILE_AUTH_TOKEN = $script:Token

$stdout = Join-Path $logDir "backend.stdout.log"
$stderr = Join-Path $logDir "backend.stderr.log"
$process = Start-Process -FilePath $BackendPython -ArgumentList @(
    "-m", "uvicorn", "main:app",
    "--host", "127.0.0.1",
    "--port", "$port"
) -WorkingDirectory $backendDir -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru

try {
    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-RestMethod "$script:BaseUrl/api/health" -TimeoutSec 2 | Out-Null
            $healthy = $true
        } catch {
            $healthy = $false
        }
    } while (-not $healthy -and (Get-Date) -lt $deadline)

    if (-not $healthy) {
        throw "Backend did not become healthy. Logs: $logDir"
    }

    $cases = @(
        @{ name = "eo_no_geo"; folder = $cfg.eo_no_geo; expectGeo = $false; checkBinary = $false },
        @{ name = "eo_geo"; folder = $cfg.eo_geo; expectGeo = $true; checkBinary = $false },
        @{ name = "sar_16bit_geo"; folder = $cfg.sar_16bit_geo; expectGeo = $true; checkBinary = $true }
    )

    foreach ($case in $cases) {
        $caseName = $case["name"]
        Write-Host "Smoke case: $caseName"
        $project = Invoke-Api POST "/projects/" @{
            name = "smoke-$caseName"
            scene_folder = $case["folder"]
            classes_file = $cfg.classes_json
        }
        $projectId = $project.id
        $scenes = Invoke-Api GET "/projects/$projectId/scenes/"
        if (-not $scenes -or $scenes.Count -lt 1) {
            throw "No scenes listed for $caseName"
        }

        $scene = Invoke-Api GET "/projects/$projectId/scenes/$($scenes[0].id)"
        if ($case["expectGeo"] -and -not $scene.scene_info.has_geo) {
            throw "$caseName should be georeferenced"
        }

        $thumb = Join-Path $tempRoot "$caseName-thumb.png"
        Invoke-Api GET "/projects/$projectId/scenes/$($scene.id)/thumbnail" -OutFile $thumb | Out-Null

        $tileInfo = Invoke-Api GET "/projects/$projectId/scenes/$($scene.id)/scene-tiles/info"
        $tile = Join-Path $tempRoot "$caseName-tile.png"
        Invoke-Api GET "/projects/$projectId/scenes/$($scene.id)/scene-tiles/$($tileInfo.max_zoom)/0/0.png" -OutFile $tile | Out-Null
        if ($case["checkBinary"]) {
            Assert-ImageNotBinary $tile
        }

        if ($scene.scene_info.has_geo -and $scene.scene_info.bounds) {
            $bounds = $scene.scene_info.bounds
            $lon = ([double]$bounds[0] + [double]$bounds[2]) / 2.0
            $lat = ([double]$bounds[1] + [double]$bounds[3]) / 2.0
            $xy = Get-TileXY -Lon $lon -Lat $lat -Zoom 12
            $geoTile = Join-Path $tempRoot "$caseName-geo-tile.png"
            Invoke-Api GET "/projects/$projectId/scenes/$($scene.id)/geo-tiles/12/$($xy.x)/$($xy.y).png" -OutFile $geoTile | Out-Null
        }

        Invoke-WebRequest -Method POST -Uri "$script:BaseUrl/api/projects/$projectId/tiling/execute?token=$script:Token" -TimeoutSec 600 | Out-Null
        Invoke-WebRequest -Method POST -Uri "$script:BaseUrl/api/projects/$projectId/dataset/generate?token=$script:Token" -TimeoutSec 600 | Out-Null
        Invoke-Api POST "/projects/$projectId/export/yolo" | Out-Null
        Invoke-Api POST "/projects/$projectId/export/coco" | Out-Null
        Invoke-Api POST "/projects/$projectId/export/voc" | Out-Null
    }

    Write-Host "Backend smoke test passed."
} finally {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
}
