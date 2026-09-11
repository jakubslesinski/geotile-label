param(
    [Parameter()]
    [string]$RepoRoot = "",

    [Parameter()]
    [string]$Address = "127.0.0.1:8088",

    [Parameter()]
    [switch]$LiveEdit
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot)

& (Join-Path $PSScriptRoot "build-docs.ps1") -RepoRoot $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Documentation build failed with exit code $LASTEXITCODE"
}

$venvPython = Join-Path $RepoRoot ".docs-build\venv\Scripts\python.exe"
$configPath = Join-Path $RepoRoot "mkdocs.yml"
$tauriConfigPath = Join-Path $RepoRoot "frontend\src-tauri\tauri.conf.json"
$tauriConfig = [System.IO.File]::ReadAllText($tauriConfigPath) | ConvertFrom-Json
$env:GEOTILE_DOCS_VERSION = [string]$tauriConfig.version

if ($LiveEdit) {
    if ($Address -notmatch "^127\.0\.0\.1:\d+$") {
        throw "Live Edit may only be served on 127.0.0.1. Use an address such as 127.0.0.1:8088."
    }

    $liveConfigPath = Join-Path $RepoRoot "mkdocs.live.yml"
    $liveRequirementsPath = Join-Path $RepoRoot "docs-live-requirements.txt"
    $liveRequirementsMarker = Join-Path $RepoRoot ".docs-build\venv\.live-edit-requirements.sha256"

    foreach ($requiredPath in @($liveConfigPath, $liveRequirementsPath)) {
        if (-not (Test-Path -LiteralPath $requiredPath)) {
            throw "Required Live Edit file was not found: $requiredPath"
        }
    }

    $liveRequirementsHash = (Get-FileHash -LiteralPath $liveRequirementsPath -Algorithm SHA256).Hash
    $installedLiveRequirementsHash = if (Test-Path -LiteralPath $liveRequirementsMarker) {
        (Get-Content -LiteralPath $liveRequirementsMarker -Raw).Trim()
    } else {
        ""
    }

    if ($liveRequirementsHash -ne $installedLiveRequirementsHash) {
        Write-Host "Installing pinned MkDocs Live Edit dependency"
        & $venvPython -m pip install --disable-pip-version-check -r $liveRequirementsPath
        if ($LASTEXITCODE -ne 0) {
            throw "MkDocs Live Edit dependency installation failed with exit code $LASTEXITCODE"
        }
        [System.IO.File]::WriteAllText(
            $liveRequirementsMarker,
            $liveRequirementsHash,
            (New-Object System.Text.UTF8Encoding -ArgumentList $false)
        )
    }

    $configPath = $liveConfigPath
    Write-Warning "Live Edit can modify, rename and delete Markdown files in docs/. Keep this server local."
}

Write-Host "Documentation server: http://$Address"
& $venvPython -m mkdocs serve --config-file $configPath --dev-addr $Address
if ($LASTEXITCODE -ne 0) {
    throw "MkDocs development server failed with exit code $LASTEXITCODE"
}
