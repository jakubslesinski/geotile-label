<#
    Buduje dystrybuowalny pakiet runtime CUDA.

    Pakiet jest wskazywany recznie w aplikacji (Ustawienia -> Pakiet treningowy GPU),
    wiec nie trafia do instalatora i nie wymaga infrastruktury hostingowej. Powod,
    dla ktorego CUDA nie jest w instalatorze: ani NSIS (failed creating mmap), ani
    WiX (light.exe) nie zapakuja ~3 GB payloadu - patrz DESIGN_DECISIONS.md, cuda-pack.

    Wynik: katalog release/cuda-pack-<wersja>/ z archiwum (opcjonalnie pocietym),
    sumami SHA-256 i README dla odbiorcy.

    UWAGA: ten plik musi byc zapisany jako UTF-8 z BOM. PowerShell 5.1 czyta skrypty
    bez BOM jako ANSI, co psuje polskie znaki w generowanym README.
#>
param(
    [Parameter()]
    [string]$RepoRoot = "",

    # Pominiecie budowy runtime, gdy archiwum studio juz istnieje.
    [Parameter()]
    [string]$FromArchive = "",

    # 0 = jeden plik. Wartosc > 0 tnie pakiet na czesci tej wielkosci (MB), co bywa
    # potrzebne, gdy nosnik albo kanal przesylu ma ograniczenie rozmiaru pliku.
    [Parameter()]
    [ValidateRange(0, 4000)]
    [int]$PartSizeMB = 0,

    [Parameter()]
    [string]$CudaIndexUrl = "https://download.pytorch.org/whl/cu126",

    # Pomija dolaczenie wag bazowych. Uzyteczne, gdy wydajesz sam runtime, a wagi u
    # odbiorcy juz sa - nie ma sensu przesylac 0,5 GB drugi raz.
    [Parameter()]
    [switch]$SkipBaseModels
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

function Write-Utf8NoBom($Path, $Value) {
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

# Wersja pakietu odpowiada wersji aplikacji - runtime i aplikacja sa wydawane razem.
$tauriConf = Join-Path $RepoRoot "frontend\src-tauri\tauri.conf.json"
$version = (Get-Content $tauriConf -Raw | ConvertFrom-Json).version
Write-Host "Wersja aplikacji: $version"

$buildRoot = Join-Path $RepoRoot ".desktop-build\cuda-pack-build"
$outputDir = Join-Path $RepoRoot "release\cuda-pack-$version"

if ($FromArchive) {
    $archive = (Resolve-Path $FromArchive).Path
    Write-Host "Uzywam istniejacego archiwum: $archive"
} else {
    Write-Host "Buduje runtime studio (pobranie kol CUDA to ~3 GB)..."
    & (Join-Path $PSScriptRoot "build-backend-env.ps1") `
        -RepoRoot $RepoRoot -Variant studio -BuildRoot $buildRoot `
        -SkipResourceCopy -CudaIndexUrl $CudaIndexUrl
    $archive = Join-Path $buildRoot "backend-env.tar.gz"
}

if (-not (Test-Path $archive)) {
    throw "Nie znaleziono archiwum runtime: $archive"
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
foreach ($existing in [System.IO.Directory]::GetFiles($outputDir)) {
    [System.IO.File]::Delete($existing)
}

$baseName = "geotile-cuda-runtime-$version.tar.gz"
$produced = @()

if ($PartSizeMB -gt 0) {
    Write-Host "Tne pakiet na czesci po $PartSizeMB MB..."
    $inputStream = [System.IO.File]::OpenRead($archive)
    try {
        $buffer = New-Object byte[] (8 * 1024 * 1024)
        [long]$partSize = [long]$PartSizeMB * 1MB
        $index = 1
        while ($inputStream.Position -lt $inputStream.Length) {
            $partPath = Join-Path $outputDir ("{0}.{1:D3}" -f $baseName, $index)
            $outputStream = [System.IO.File]::Create($partPath)
            try {
                [long]$written = 0
                while ($written -lt $partSize) {
                    $toRead = [int][Math]::Min([long]$buffer.Length, $partSize - $written)
                    $read = $inputStream.Read($buffer, 0, $toRead)
                    if ($read -le 0) { break }
                    $outputStream.Write($buffer, 0, $read)
                    $written += $read
                }
            } finally { $outputStream.Dispose() }
            $produced += $partPath
            $index++
        }
    } finally { $inputStream.Dispose() }
} else {
    $target = Join-Path $outputDir $baseName
    Copy-Item $archive $target -Force
    $produced += $target
}

# --- Wagi bazowe -----------------------------------------------------------------
# Osobne archiwum, nie czesc runtime: wagi zmieniaja sie niezaleznie od CUDA, a
# przepakowanie ~3 GB runtime tylko po to, by dolozyc 0,5 GB wag, byloby marnotrawstwem.
# Aplikacja rozpoznaje je po fragmencie "base-models" w nazwie i instaluje do
# MODELS_ROOT\base, gdy uzytkownik wskaze oba pliki naraz.
if (-not $SkipBaseModels) {
    $weightsDir = Join-Path $RepoRoot "data\models\base"
    $weightFiles = @()
    if (Test-Path $weightsDir) {
        $weightFiles = @(Get-ChildItem $weightsDir -Filter "*.pt" -File)
    }

    if ($weightFiles.Count -eq 0) {
        Write-Warning "Brak wag bazowych w $weightsDir - pakiet powstanie bez nich."
        Write-Warning "Przygotuj je przez scripts\fetch-base-models.ps1 albo uzyj -SkipBaseModels swiadomie."
    }
    else {
        $weightsArchive = Join-Path $outputDir "geotile-base-models-$version.tar.gz"
        Write-Host ("Pakuje {0} plikow wag bazowych..." -f $weightFiles.Count)

        # Jawna sciezka do bsdtar z Windows. Samo "tar.exe" bywa rozwiazywane na GNU tar
        # z Git Bash, gdy jest wczesniej w PATH - a ten traktuje "C:\..." jako nazwe
        # zdalnego hosta ("Cannot connect to C: resolve failed") i pakowanie pada.
        $tarExe = Join-Path $env:SystemRoot "System32\tar.exe"
        if (-not (Test-Path $tarExe)) {
            throw "Nie znaleziono $tarExe. Pakowanie wag wymaga tar z Windows 10/11."
        }

        # -C sprawia, ze pliki leza w korzeniu archiwum, bez sciezki katalogu zrodlowego.
        # base_models_index.json to lokalny cache haszy budowany przez backend przy
        # listowaniu katalogu - dotyczy tej maszyny, nie odbiorcy, wiec nie jedzie dalej.
        & $tarExe -czf $weightsArchive -C $weightsDir --exclude "./base_models_index.json" "."
        if ($LASTEXITCODE -ne 0) { throw "Pakowanie wag bazowych nie powiodlo sie (tar: $LASTEXITCODE)." }
        $produced += $weightsArchive
        Write-Host ("Wagi bazowe            : {0:N2} GB" -f ((Get-Item $weightsArchive).Length / 1GB))
    }
}

$lines = @()
foreach ($file in $produced) {
    $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
    $name = [System.IO.Path]::GetFileName($file)
    $lines += "$hash  $name"
    Write-Host ("  {0}  {1:N2} GB" -f $name, ((Get-Item $file).Length / 1GB))
}
Write-Utf8NoBom (Join-Path $outputDir "SHA256SUMS.txt") (($lines -join "`n") + "`n")

# Szablon literalny (bez rozwijania) - dzieki temu backticki blokow kodu markdown
# nie sa traktowane jako znaki ucieczki PowerShella.
$readmeTemplate = @'
# GeoTile Label — pakiet treningowy GPU {VERSION}

Pakiet zawiera środowisko uruchomieniowe z PyTorch skompilowanym pod CUDA oraz wagi
bazowe architektur i jest potrzebny **wyłącznie do trenowania modeli**. Instalacja
bazowa aplikacji działa na CPU i obejmuje labelowanie, SAM, egzemplarz oraz
predykcję — do tych funkcji pakiet nie jest wymagany.

## Zawartość

{FILES}

Plik z `base-models` w nazwie zawiera wagi bazowe (punkty startowe treningu). Jest
niezależny od runtime — instalowany razem z nim, ale może też zostać wskazany sam,
gdy runtime jest już zainstalowany, a doszły nowe architektury.

## Instalacja

1. Skopiuj pliki na komputer z kartą NVIDIA.
2. W aplikacji otwórz **Ustawienia → Pakiet treningowy GPU**.
3. Wybierz **Wskaż pakiet CUDA** i zaznacz **wszystkie pliki naraz**, łącznie z
   archiwum wag bazowych.
4. Po zainstalowaniu **uruchom aplikację ponownie**.

Bez wag bazowych zakładka Trening pokaże architektury jako niedostępne z powodem
„Weights are missing locally" — sama instalacja runtime nie wystarczy.

Aplikacja sprawdza pakiet po rozpakowaniu, uruchamiając sondę. Jeśli pakiet nie
działa, zostaje usunięty, a aplikacja dalej korzysta z runtime CPU — nieudana
instalacja nie może zepsuć działającej aplikacji.

## Weryfikacja

Sumy kontrolne znajdują się w pliku SHA256SUMS.txt. Sprawdzenie w PowerShell:

    Get-FileHash .\{FIRSTFILE} -Algorithm SHA256

## Wymagania

- karta NVIDIA ze sterownikiem obsługującym CUDA 12.6,
- około 6,5 GB wolnego miejsca na dysku,
- ta sama wersja aplikacji: **{VERSION}**.
'@

$fileList = ($produced | ForEach-Object { "- " + [System.IO.Path]::GetFileName($_) }) -join "`n"
$fileWord = if ($produced.Count -gt 1) { "pliki (zaznacz wszystkie czesci naraz)" } else { "plik" }
$readme = $readmeTemplate.
    Replace("{VERSION}", $version).
    Replace("{FILES}", $fileList).
    Replace("{FILEWORD}", $fileWord).
    Replace("{FIRSTFILE}", [System.IO.Path]::GetFileName($produced[0]))
Write-Utf8NoBom (Join-Path $outputDir "README.md") $readme

Write-Host ""
Write-Host "Pakiet gotowy: $outputDir"
