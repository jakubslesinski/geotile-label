param(
    [Parameter()]
    [string]$RepoRoot = "",

    # Odrzuca build, jesli w dokumentacji zostal choc jeden placeholder materialu
    # wizualnego. Placeholdery sa dozwolone w trakcie pisania (etap D1a), ale nie
    # moga trafic do wydania - uzywa tego build release.
    [Parameter()]
    [switch]$RequireAssets
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)
$outputDir = Join-Path $RepoRoot ".docs-build\validation-site"

# Bramka dryfu diagramow architektury. Idzie PRZED buildem dokumentacji, bo wykrywa
# nieaktualny `docs/architecture/explorer.html` - plik generowany z modelu tresci. Gdyby
# szla po buildzie, dokumentacja zdazylaby sie zbudowac ze starej tresci, a i tak
# skonczyloby sie bledem. Sprawdzenie trwa ulamek sekundy i uzywa samej biblioteki
# standardowej, wiec nie potrzebuje venva dokumentacji.
#
# Rysunki artykulu leza w OSOBNYM repozytorium (`geotile-label-paper`), ktorego nie musi
# byc na maszynie budujacej wydanie - bramka sama je wtedy pomija i sprawdza tylko model
# oraz explorer. Sciezke mozna wskazac przez GEOTILE_PAPER_ROOT.
Write-Host "Checking architecture diagram drift"
$architectureGate = Join-Path $PSScriptRoot "check-architecture-model.py"
$docsVenvPython = Join-Path $RepoRoot ".docs-build/venv/Scripts/python.exe"
if (Test-Path -LiteralPath $docsVenvPython) {
    $gateExe = $docsVenvPython
    $gateArgs = @($architectureGate)
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $gateExe = "py"
    $gateArgs = @("-3", $architectureGate)
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $gateExe = "python"
    $gateArgs = @($architectureGate)
} else {
    throw "Python 3 was not found in PATH. It is required to check architecture diagrams."
}
& $gateExe @gateArgs
if ($LASTEXITCODE -ne 0) {
    throw ("Architecture diagrams drifted from docs/architecture/architecture-model.json. " +
        "Regenerate with scripts/generate-architecture-explorer.py and " +
        "scripts/generate-architecture-d2.py, or update the model.")
}

# Walidacja buduje do wlasnego katalogu i nie moze podmieniac zasobow frontendu.
& (Join-Path $PSScriptRoot "build-docs.ps1") -RepoRoot $RepoRoot -OutputDir $outputDir -NoFrontendSync
if ($LASTEXITCODE -ne 0) {
    throw "Documentation build failed with exit code $LASTEXITCODE"
}

$indexPath = Join-Path $outputDir "index.html"
$englishIndexPath = Join-Path $outputDir "en\index.html"
$buildInfoPath = Join-Path $outputDir "build-info.json"
$englishBuildInfoPath = Join-Path $outputDir "en\build-info.json"
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "Documentation index is missing: $indexPath"
}
if (-not (Test-Path -LiteralPath $buildInfoPath)) {
    throw "Documentation build metadata is missing: $buildInfoPath"
}
if (-not (Test-Path -LiteralPath $englishIndexPath)) {
    throw "English documentation index is missing: $englishIndexPath"
}
if (-not (Test-Path -LiteralPath $englishBuildInfoPath)) {
    throw "English documentation build metadata is missing: $englishBuildInfoPath"
}

$searchAssets = Get-ChildItem -LiteralPath $outputDir -Recurse -File | Where-Object {
    $_.FullName -match "[\\/]search[\\/]" -and $_.Extension -in @(".js", ".json")
}
if (-not $searchAssets) {
    throw "Offline search assets were not generated."
}

$htmlFiles = Get-ChildItem -LiteralPath $outputDir -Recurse -Filter "*.html" -File
$externalResourcePattern = '<(?:script|img)[^>]+src=["'']https?://|<link[^>]+href=["'']https?://'
$externalResources = $htmlFiles | Select-String -Pattern $externalResourcePattern
if ($externalResources) {
    $matches = ($externalResources | Select-Object -First 5 | ForEach-Object { $_.Path }) -join ", "
    throw "External runtime assets were detected in generated HTML: $matches"
}

$buildInfo = [System.IO.File]::ReadAllText($buildInfoPath) | ConvertFrom-Json
$englishBuildInfo = [System.IO.File]::ReadAllText($englishBuildInfoPath) | ConvertFrom-Json
$indexHtml = [System.IO.File]::ReadAllText($indexPath)
$englishIndexHtml = [System.IO.File]::ReadAllText($englishIndexPath)
if ($indexHtml -notmatch [regex]::Escape([string]$buildInfo.app_version)) {
    throw "Application version is not visible in the generated documentation."
}
if ($englishIndexHtml -notmatch [regex]::Escape([string]$englishBuildInfo.app_version)) {
    throw "Application version is not visible in the generated English documentation."
}
if ([string]$buildInfo.language -ne "pl" -or [string]$englishBuildInfo.language -ne "en") {
    throw "Documentation language metadata is invalid."
}

# --- Postep pisania (etap D1a) ----------------------------------------------------
# Szkielety stron licza sie po markerze z scaffoldu. Sluzy to wylacznie za miernik
# postepu - build przechodzi ze szkieletami, bo linki do nich musza dzialac od
# poczatku, inaczej strict odrzuca kazde odwolanie w przod.
$docsDirForStubs = Join-Path $RepoRoot "docs"
if (Test-Path -LiteralPath $docsDirForStubs) {
    $allPages = @(Get-ChildItem -LiteralPath $docsDirForStubs -Recurse -Filter "*.md" -File)
    $stubPages = @($allPages | Where-Object {
        (Get-Content -LiteralPath $_.FullName -Raw) -match 'Strona w przygotowaniu'
    })
    $written = $allPages.Count - $stubPages.Count
    Write-Host ""
    Write-Host ("Postep D1a: {0} z {1} stron napisanych, {2} w przygotowaniu" -f `
        $written, $allPages.Count, $stubPages.Count)

    # Strony napisane "na sucho", bez przejscia przez aplikacje. Tresc jest kompletna,
    # ale przebieg wymaga potwierdzenia - inaczej niz szkielet, ktory jest pusty.
    $draftPages = @($allPages | Where-Object {
        (Get-Content -LiteralPath $_.FullName -Raw) -match 'Szkic do weryfikacji'
    })
    if ($draftPages.Count -gt 0) {
        Write-Host ("Szkice do weryfikacji w aplikacji: {0}" -f $draftPages.Count)
        foreach ($page in $draftPages) {
            $relative = $page.FullName.Substring($RepoRoot.Length).TrimStart('\', '/')
            Write-Host ("       {0}" -f $relative)
        }
    }
}

# --- Inwentarz placeholderow ------------------------------------------------------
# Liczymy w zrodlach (docs/), nie w wyniku buildu: zrodlo jest tym, co autor poprawia,
# a nazwa pliku placeholdera jest w wyniku i tak przepisana bez zmian.
$docsDir = Join-Path $RepoRoot "docs"
$placeholderPattern = "_placeholder\.svg"
$placeholderHits = @()
if (Test-Path -LiteralPath $docsDir) {
    $placeholderHits = @(
        Get-ChildItem -LiteralPath $docsDir -Recurse -Filter "*.md" -File |
            Select-String -Pattern $placeholderPattern
    )
}

if ($placeholderHits.Count -gt 0) {
    $byPage = $placeholderHits | Group-Object { $_.Path } | Sort-Object Name
    Write-Host ""
    Write-Host "Materialy wizualne w przygotowaniu: $($placeholderHits.Count) w $($byPage.Count) plikach"
    foreach ($page in $byPage) {
        $relative = $page.Name.Substring($RepoRoot.Length).TrimStart('\', '/')
        Write-Host ("  {0,3}  {1}" -f $page.Count, $relative)
    }

    if ($RequireAssets) {
        throw "Dokumentacja zawiera $($placeholderHits.Count) placeholderow materialow wizualnych. Wydanie wymaga kompletnych assetow (etap D1b)."
    }
    Write-Host "Placeholdery sa dozwolone poza wydaniem. Bramka: -RequireAssets."
    Write-Host ""
}

Write-Host "Documentation validation passed for version $($buildInfo.app_version)."
