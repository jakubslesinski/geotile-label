param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter()]
    [string]$RepoRoot = "",

    [Parameter()]
    [switch]$SkipSmokeTests,

    [Parameter()]
    [switch]$ReuseBackendRuntime,

    # Przepuszcza build mimo placeholderow materialow wizualnych w dokumentacji
    # (etap D1b nieukonczony). Wylacznie do buildow testowych - nie do wydania.
    [Parameter()]
    [switch]$AllowDocsPlaceholders,

    # Edycja "lite" dla uzytkownikow koncowych: ukrywa zakladki Analiza datasetu,
    # Trening i Wyniki (VITE_GEOTILE_EDITION=lite). Wynik trafia do osobnego katalogu
    # release/...-lite, zeby nie nadpisac pelnej edycji.
    [Parameter()]
    [switch]$Lite
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

$frontendDir = Join-Path $RepoRoot "frontend"
$nsisDir = Join-Path $RepoRoot "frontend\src-tauri\target\release\bundle\nsis"
$releaseRoot = Join-Path $RepoRoot "release"
$editionSuffix = if ($Lite) { "-lite" } else { "" }
$releaseName = "GeoTileLabel-$Version$editionSuffix"
$releaseDir = Join-Path $releaseRoot $releaseName

function Write-Utf8NoBom($Path, $Value) {
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function Write-JsonFile($Path, $Object) {
    Write-Utf8NoBom $Path (($Object | ConvertTo-Json -Depth 100) + "`n")
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

& (Join-Path $PSScriptRoot "set-version.ps1") -Version $Version -RepoRoot $RepoRoot

& (Join-Path $PSScriptRoot "prepare-tauri-backend.ps1") -RepoRoot $RepoRoot
# Runtime trafia do zasobow jako czesci backend-env.tar.gz.NNN (limit rozmiaru
# pojedynczego zasobu w NSIS), wiec sprawdzamy czesci, nie pojedynczy plik.
$resourcesDir = Join-Path $RepoRoot "frontend\src-tauri\resources"
if ($ReuseBackendRuntime) {
    $existingParts = @(Get-ChildItem $resourcesDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "backend-env.tar.gz*" })
    if ($existingParts.Count -eq 0) {
        throw "Cannot reuse the backend runtime because no backend-env.tar.gz part exists in $resourcesDir"
    }
    Write-Host ("Reusing existing backend runtime ({0} part(s))" -f $existingParts.Count)
} else {
    & (Join-Path $PSScriptRoot "build-backend-env.ps1") -RepoRoot $RepoRoot
}

if (-not $SkipSmokeTests) {
    Write-Host "Running release preflight smoke tests with the packaged backend runtime"
    & (Join-Path $PSScriptRoot "smoke-project-migration.ps1")
    & (Join-Path $PSScriptRoot "smoke-scene-resolvers.ps1") -RequireJp2
    & (Join-Path $PSScriptRoot "smoke-scene-identity.ps1")
    & (Join-Path $PSScriptRoot "smoke-scene-preparation.ps1")
    & (Join-Path $PSScriptRoot "smoke-tile-catalog.ps1")
    & (Join-Path $PSScriptRoot "smoke-dataset-runs.ps1")
}

# Dokumentacja musi powstac przed buildem frontendu: Vite kopiuje frontend/public/help
# do dist, a Tauri pakuje dist do instalatora. Odwrotna kolejnosc daje instalator bez
# dokumentacji i przycisk Pomoc z bledem.
Write-Host "Building offline documentation"
& (Join-Path $PSScriptRoot "build-docs.ps1") -RepoRoot $RepoRoot
if ($AllowDocsPlaceholders) {
    Write-Warning "Documentation placeholders are allowed for this build. Do not ship this installer to users."
    & (Join-Path $PSScriptRoot "validate-docs.ps1") -RepoRoot $RepoRoot
} else {
    & (Join-Path $PSScriptRoot "validate-docs.ps1") -RepoRoot $RepoRoot -RequireAssets
}

if (Test-Path $nsisDir) {
    Remove-Item (Join-Path $nsisDir "*.exe") -Force -ErrorAction SilentlyContinue
}

Push-Location $frontendDir
$previousBuildVariant = $env:GEOTILE_BUILD_VARIANT
$previousEdition = $env:VITE_GEOTILE_EDITION
try {
    $env:GEOTILE_BUILD_VARIANT = "yolo"
    # Vite czyta VITE_* z otoczenia przy buildzie; "lite" ukrywa zaawansowane zakladki.
    $env:VITE_GEOTILE_EDITION = if ($Lite) { "lite" } else { "" }
    if ($Lite) { Write-Host "Edycja: LITE (bez zakladek Analiza/Trening/Wyniki)" }
    Invoke-Checked "npm.cmd" run tauri:build
} finally {
    $env:GEOTILE_BUILD_VARIANT = $previousBuildVariant
    $env:VITE_GEOTILE_EDITION = $previousEdition
    Pop-Location
}

$installer = Get-ChildItem $nsisDir -Filter "*$Version*_x64-setup.exe" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if (-not $installer) {
    throw "NSIS installer for version $Version was not found in $nsisDir"
}

if (Test-Path $releaseDir) {
    Remove-Item $releaseDir -Recurse -Force
}
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

# Dla edycji "lite" dokladamy sufiks do nazwy pliku instalatora, zeby obie edycje tej
# samej wersji dalo sie odroznic po samym pliku (NSIS nazywa je tak samo).
$installerFileName = if ($Lite) { $installer.BaseName + "-lite" + $installer.Extension } else { $installer.Name }
$installerTarget = Join-Path $releaseDir $installerFileName
Copy-Item $installer.FullName $installerTarget
$readmeTarget = Join-Path $releaseDir "README.md"
Copy-Item (Join-Path $RepoRoot "README.md") $readmeTarget
$releaseManifestPath = Join-Path $releaseDir "release_manifest.json"
Write-JsonFile $releaseManifestPath ([ordered]@{
    schema_name = "geotile_desktop_release"
    schema_version = 1
    version = $Version
    variant = "unified"
    edition = if ($Lite) { "lite" } else { "full" }
    platform = "windows-x64"
    installer = $installerFileName
    generated_at = [DateTime]::UtcNow.ToString("o")
    distribution = "tauri-nsis"
    bundled_backend = $true
    bundled_yolo = $true
    supported_schema_versions = [ordered]@{
        project = 2
        scene_manifest = 4
        scene_sources = 1
        annotation_package = 1
        tile_catalog = 2
        dataset_run = 2
        dataset_package = 1
    }
})

$checksumPath = Join-Path $releaseDir "SHA256SUMS.txt"
$checksumLines = @()
foreach ($path in @($installerTarget, $readmeTarget, $releaseManifestPath)) {
    $hash = Get-FileHash $path -Algorithm SHA256
    $checksumLines += "$($hash.Hash)  $([IO.Path]::GetFileName($path))"
}
$checksumLines | Set-Content -Path $checksumPath -Encoding UTF8

Write-Host ""
Write-Host "Desktop release prepared:"
Write-Host "Directory: $releaseDir"
Write-Host "Installer: $installerTarget"
Write-Host "Checksum:  $checksumPath"
