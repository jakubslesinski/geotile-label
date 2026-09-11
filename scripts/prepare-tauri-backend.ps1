param(
    [Parameter()]
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

$backendSource = Join-Path $RepoRoot "backend"
$backendTarget = Join-Path $RepoRoot "frontend\src-tauri\resources\backend"

if (-not (Test-Path (Join-Path $backendSource "main.py"))) {
    throw "Backend source not found: $backendSource"
}

if (Test-Path $backendTarget) {
    Get-ChildItem $backendTarget -Force |
        Where-Object { $_.Name -ne ".gitkeep" } |
        Remove-Item -Recurse -Force
} else {
    New-Item -ItemType Directory -Path $backendTarget -Force | Out-Null
}

$robocopyArgs = @(
    $backendSource,
    $backendTarget,
    "/E",
    "/XD", "__pycache__", ".venv", "venv", ".pytest_cache",
    "/XF", "*.pyc", "*.pyo", "*.log"
)

robocopy @robocopyArgs | Out-Host
if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

# Kod repo DINO (architektura backbone'u embeddingow) jedzie z aplikacja obok backendu, zeby
# DINO dzialal offline out-of-the-box. Sam kod jest maly (~25 MB); wagi .pth dostarcza
# uzytkownik osobno do MODELS_ROOT/dino. Loader szuka go w backend/vendor/dino - patrz
# services/embedding_backbone.py (_BUNDLED_DINO_DIR).
$dinoSource = Join-Path $RepoRoot "data\models\dino"
$dinoTarget = Join-Path $backendTarget "vendor\dino"
$dinoRepos = @("dinov2_repo", "dinov3_repo")
$bundledDino = @()
foreach ($repo in $dinoRepos) {
    $src = Join-Path $dinoSource $repo
    if (Test-Path $src) {
        $dst = Join-Path $dinoTarget $repo
        # Loader potrzebuje TYLKO kodu pakietu (dinov2/ lub dinov3/ + hubconf.py). Reszta repo
        # to balast: notebooki (base64-obrazy, ~20 MB), docs, scripts, testy, obrazy. Solid-LZMA
        # instalatora dlawil sie na tym (makensis OOM). Wykluczamy ciezkie katalogi i typy plikow.
        robocopy $src $dst "/E" `
            "/XD" ".git" "__pycache__" "notebooks" "docs" "scripts" "tests" ".github" "assets" "examples" `
            "/XF" "*.ipynb" "*.png" "*.jpg" "*.jpeg" "*.gif" "*.pdf" "*.mp4" | Out-Host
        if ($LASTEXITCODE -ge 8) { throw "robocopy DINO ($repo) failed with exit code $LASTEXITCODE" }
        $bundledDino += $repo
    }
}
if ($bundledDino.Count -gt 0) {
    Write-Host "Bundled DINO repo code: $($bundledDino -join ', ') -> $dinoTarget"
} else {
    Write-Warning "No DINO repo code in $dinoSource - build will ship WITHOUT offline DINO. Run scripts/fetch-dino-repos.ps1 first to enable Dataset analysis (DI2)."
}

Write-Host "Backend source prepared for Tauri resources: $backendTarget"
