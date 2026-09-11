param(
    [Parameter()]
    [string]$RepoRoot = "",

    [Parameter()]
    [string]$OutputDir = ".docs-build\site",

    [Parameter()]
    [switch]$ForceDependencies,

    # Gotowe strony trafiaja do frontend/public/help, skad Vite kopiuje je do dist,
    # a Tauri pakuje razem z aplikacja. Wylacz tylko wtedy, gdy budujesz sama paczke
    # dokumentacji i nie chcesz dotykac frontendu.
    [Parameter()]
    [switch]$NoFrontendSync
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)

$requirementsPath = Join-Path $RepoRoot "docs-requirements.txt"
$configPath = Join-Path $RepoRoot "mkdocs.yml"
$tauriConfigPath = Join-Path $RepoRoot "frontend\src-tauri\tauri.conf.json"
$buildRoot = Join-Path $RepoRoot ".docs-build"
$venvRoot = Join-Path $buildRoot "venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$requirementsMarker = Join-Path $venvRoot ".requirements.sha256"

if (-not [System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot $OutputDir
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)

foreach ($requiredPath in @($requirementsPath, $configPath, $tauriConfigPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required documentation file was not found: $requiredPath"
    }
}

function Invoke-CommandChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        Invoke-CommandChecked -FilePath $pyLauncher.Source -Arguments @("-3", "-m", "venv", $venvRoot)
    } else {
        $pythonLauncher = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonLauncher) {
            throw "Python 3 was not found in PATH. It is required only to build the documentation."
        }
        Invoke-CommandChecked -FilePath $pythonLauncher.Source -Arguments @("-m", "venv", $venvRoot)
    }
}

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$installedHash = if (Test-Path -LiteralPath $requirementsMarker) {
    (Get-Content -LiteralPath $requirementsMarker -Raw).Trim()
} else {
    ""
}

if ($ForceDependencies -or $requirementsHash -ne $installedHash) {
    Write-Host "Installing pinned MkDocs dependencies"
    Invoke-CommandChecked -FilePath $venvPython -Arguments @(
        "-m", "pip", "install", "--disable-pip-version-check", "-r", $requirementsPath
    )
    [System.IO.File]::WriteAllText(
        $requirementsMarker,
        $requirementsHash,
        (New-Object System.Text.UTF8Encoding -ArgumentList $false)
    )
}

$tauriConfigText = [System.IO.File]::ReadAllText($tauriConfigPath)
$tauriConfig = $tauriConfigText | ConvertFrom-Json
$appVersion = [string]$tauriConfig.version
if ([string]::IsNullOrWhiteSpace($appVersion)) {
    throw "Application version is missing in $tauriConfigPath"
}

$previousVersion = $env:GEOTILE_DOCS_VERSION
try {
    $env:GEOTILE_DOCS_VERSION = $appVersion
    Write-Host "Building GeoTile Label documentation $appVersion"
    Invoke-CommandChecked -FilePath $venvPython -Arguments @(
        "-m", "mkdocs", "build", "--strict", "--clean",
        "--config-file", $configPath, "--site-dir", $OutputDir
    )
} finally {
    $env:GEOTILE_DOCS_VERSION = $previousVersion
}

$buildInfo = [ordered]@{
    schema_name = "geotile_docs_build"
    schema_version = 1
    app_version = $appVersion
    language = "pl"
    generated_at = [DateTime]::UtcNow.ToString("o")
    offline = $true
}
$buildInfoJson = $buildInfo | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText(
    (Join-Path $OutputDir "build-info.json"),
    $buildInfoJson + "`n",
    (New-Object System.Text.UTF8Encoding -ArgumentList $false)
)

$englishOutputDir = Join-Path $OutputDir "en"
$englishBuildInfo = [ordered]@{
    schema_name = "geotile_docs_build"
    schema_version = 1
    app_version = $appVersion
    language = "en"
    generated_at = [DateTime]::UtcNow.ToString("o")
    offline = $true
}
[System.IO.File]::WriteAllText(
    (Join-Path $englishOutputDir "build-info.json"),
    (($englishBuildInfo | ConvertTo-Json -Depth 4) + "`n"),
    (New-Object System.Text.UTF8Encoding -ArgumentList $false)
)

if (-not (Test-Path -LiteralPath (Join-Path $OutputDir "index.html"))) {
    throw "Documentation build did not produce index.html in $OutputDir"
}
if (-not (Test-Path -LiteralPath (Join-Path $englishOutputDir "index.html"))) {
    throw "Documentation build did not produce the English index in $englishOutputDir"
}

Write-Host "Documentation built: $OutputDir"

if (-not $NoFrontendSync) {
    $frontendHelpDir = Join-Path $RepoRoot "frontend\public\help"
    # Pelne zastapienie, nie nadpisanie: strona usunieta z nav nie moze zostac
    # w instalatorze jako osierocony plik osiagalny z wyszukiwarki.
    if (Test-Path -LiteralPath $frontendHelpDir) {
        Remove-Item -LiteralPath $frontendHelpDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $frontendHelpDir -Force | Out-Null
    Copy-Item -Path (Join-Path $OutputDir "*") -Destination $frontendHelpDir -Recurse -Force

    if (-not (Test-Path -LiteralPath (Join-Path $frontendHelpDir "index.html"))) {
        throw "Documentation was not copied to $frontendHelpDir"
    }

    Write-Host "Documentation synced to frontend assets: $frontendHelpDir"
}
