<#
    Jednorazowe pobranie bazowych wag YOLO do MODELS_ROOT/base/.

    To jest krok DEWELOPERSKI, wykonywany na maszynie z dostepem do sieci. Aplikacja
    nigdy nie pobiera wag samodzielnie - to zlamaloby invariant offline. Wagi
    przygotowane tutaj kopiuje sie na maszyne docelowa razem z instalatorem.

    Skrypt jest zamierzenie prosty: pobiera pliki po HTTP i liczy SHA-256. Nie
    instaluje ultralytics ani nie uruchamia Pythona.
#>
param(
    [Parameter()]
    [string]$RepoRoot = "",

    # Domyslnie data/models/base w repo. Na maszynie docelowej podaj katalog
    # wskazywany przez MODELS_ROOT.
    [Parameter()]
    [string]$Destination = "",

    # Puste = wszystkie z katalogu. Inaczej lista nazw plikow.
    [Parameter()]
    [string[]]$Only = @()
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $RepoRoot "data\models\base"
}

# Lista musi odpowiadac wpisom pretrained=True w BASE_MODEL_CATALOG
# (backend/services/training_models.py). Rozjazd objawi sie jako "Brak wag lokalnie"
# mimo pobrania pliku.
#
# Tag przypiety swiadomie zamiast /releases/latest/ - pobranie ma dawac ten sam wynik
# za pol roku. Poprzednio przypiety v8.3.0 nie zawiera wag YOLO26 (404), v8.4.0
# zawiera wszystkie rodziny z katalogu.
#
# W katalogu sa tez wpisy trenowane od zera (YOLO12-OBB). Nie ma dla nich wag do
# pobrania - architektura jest w pakiecie ultralytics - wiec nie wystepuja na tej liscie.
$baseUrl = "https://github.com/ultralytics/assets/releases/download/v8.4.0"
$models = @(
    "yolo11n.pt",
    "yolo11s.pt",
    "yolo11m.pt",
    "yolo11n-obb.pt",
    "yolo11s-obb.pt",
    "yolo11m-obb.pt",
    "yolov8n.pt",
    "yolov8s.pt",
    "yolov8m.pt",
    "yolov8n-obb.pt",
    "yolov8s-obb.pt",
    "yolov8m-obb.pt",
    "yolov10n.pt",
    "yolov10s.pt",
    "yolov10m.pt",
    "yolo12n.pt",
    "yolo12s.pt",
    "yolo12m.pt",
    "yolo26n.pt",
    "yolo26s.pt",
    "yolo26m.pt",
    "yolo26n-obb.pt",
    "yolo26s-obb.pt",
    "yolo26m-obb.pt"
)

if ($Only.Count -gt 0) {
    $models = $models | Where-Object { $Only -contains $_ }
    if ($models.Count -eq 0) { throw "Zadna z podanych nazw nie wystepuje w katalogu." }
}

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Write-Host "Katalog docelowy: $Destination"
Write-Host "Licencja wag: AGPL-3.0 (Ultralytics). Uzycie komercyjne wymaga licencji Enterprise."
Write-Host ""

$lines = @()
foreach ($model in $models) {
    $target = Join-Path $Destination $model
    if (Test-Path $target) {
        Write-Host ("  {0} - juz jest, pomijam pobieranie" -f $model)
    } else {
        $url = "$baseUrl/$model"
        Write-Host ("  {0} - pobieram..." -f $model)
        try {
            Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
        } catch {
            throw "Nie udalo sie pobrac $model z $url. Szczegoly: $($_.Exception.Message)"
        }
    }
    $hash = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
    $sizeMb = [math]::Round((Get-Item $target).Length / 1MB, 1)
    Write-Host ("      {0} MB  sha256={1}" -f $sizeMb, $hash.Substring(0, 16))
    $lines += "$hash  $model"
}

$sumsPath = Join-Path $Destination "SHA256SUMS.txt"
$encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($sumsPath, (($lines -join "`n") + "`n"), $encoding)

Write-Host ""
Write-Host "Gotowe. Sumy kontrolne: $sumsPath"
Write-Host "Na maszynie docelowej skopiuj zawartosc tego katalogu do MODELS_ROOT\base\."
