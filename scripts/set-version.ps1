param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [Parameter()]
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

$packagePath = Join-Path $RepoRoot "frontend\package.json"
$packageLockPath = Join-Path $RepoRoot "frontend\package-lock.json"
$cargoTomlPath = Join-Path $RepoRoot "frontend\src-tauri\Cargo.toml"
$tauriConfigPath = Join-Path $RepoRoot "frontend\src-tauri\tauri.conf.json"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "node was not found in PATH. Node.js is required to update package JSON files."
}

function Write-Utf8NoBom($Path, $Value) {
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

$nodeScript = @'
const fs = require("fs");
const [packagePath, packageLockPath, tauriConfigPath, version] = process.argv.slice(2);

function updateJson(path, updater) {
  let text = fs.readFileSync(path, "utf8");
  if (text.charCodeAt(0) === 0xfeff) {
    text = text.slice(1);
  }
  const data = JSON.parse(text);
  updater(data);
  fs.writeFileSync(path, JSON.stringify(data, null, 2) + "\n", "utf8");
}

updateJson(packagePath, (data) => {
  data.version = version;
});

updateJson(packageLockPath, (data) => {
  data.version = version;
  if (data.packages && data.packages[""]) {
    data.packages[""].version = version;
  }
});

updateJson(tauriConfigPath, (data) => {
  data.version = version;
});
'@

$nodeScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) "geotile-set-version-$PID.cjs"
try {
    Set-Content -Path $nodeScriptPath -Value $nodeScript -Encoding UTF8
    & node $nodeScriptPath $packagePath $packageLockPath $tauriConfigPath $Version
    if ($LASTEXITCODE -ne 0) {
        throw "node failed to update package JSON files"
    }
} finally {
    Remove-Item $nodeScriptPath -Force -ErrorAction SilentlyContinue
}

# WAZNE: czytaj jako UTF-8 (jak zapisujemy). Domyslny Get-Content czyta w ANSI systemu
# (Windows-1250 dla PL), a Write-Utf8NoBom zapisuje UTF-8 — niezgodnosc kodowala polskie
# znaki w polu `authors` przy KAZDYM uruchomieniu, podwajajac je w petli (Cargo.toml urosl
# do gigabajtow i wysadzal build). UTF-8 po obu stronach = stabilny round-trip.
$cargoToml = Get-Content $cargoTomlPath -Raw -Encoding UTF8
$cargoToml = $cargoToml -replace '(?m)^version = "[^"]+"', "version = `"$Version`""
Write-Utf8NoBom $cargoTomlPath $cargoToml

Write-Host "GeoTile Label version set to $Version"
