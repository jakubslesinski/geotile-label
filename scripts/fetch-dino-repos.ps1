<#
    Jednorazowe pobranie KODU repo DINO (architektura backbone'u) do
    MODELS_ROOT/dino/{dinov2_repo,dinov3_repo}.

    To krok DEWELOPERSKI, na maszynie z siecia. Aplikacja nigdy nie pobiera kodu sama -
    zlamaloby to inwariant offline. Kod repo jest maly (dinov2 ~5 MB, dinov3 ~20 MB) i
    wedruje na maszyne docelowa razem z wagami (*.pth, ktore uzytkownik dostarcza osobno).

    Po tym kroku services/embedding_backbone.py laduje DINO offline (source="local" dla
    DINOv2, bezposredni import backbone'u dla DINOv3 - jego hubconf wymaga torchmetrics,
    wiec omijamy hubconf).

    Wagi NIE sa pobierane tym skryptem (duze, czesc bramkowana licencja - np. DINOv3-SAT).
#>
param(
    [Parameter()]
    [string]$RepoRoot = "",

    # Domyslnie data/models/dino w repo. Na maszynie docelowej podaj katalog dino pod MODELS_ROOT.
    [Parameter()]
    [string]$Destination = "",

    # Puste = oba. Inaczej: "dinov2" i/lub "dinov3".
    [Parameter()]
    [string[]]$Only = @()
)

$ErrorActionPreference = "Stop"

if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path }
if (-not $Destination) { $Destination = Join-Path $RepoRoot "data\models\dino" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git nie jest dostepny w PATH - wymagany do pobrania repo DINO."
}

# Repozytoria kodu (publiczne; wagi bramkowane, ale KOD nie). Piny commitow mozna dodac
# pozniej dla powtarzalnosci; na razie plytki klon domyslnej galezi.
$repos = @(
    @{ name = "dinov2"; url = "https://github.com/facebookresearch/dinov2.git"; dir = "dinov2_repo" },
    @{ name = "dinov3"; url = "https://github.com/facebookresearch/dinov3.git"; dir = "dinov3_repo" }
)

foreach ($repo in $repos) {
    if ($Only.Count -gt 0 -and ($Only -notcontains $repo.name)) { continue }
    $target = Join-Path $Destination $repo.dir
    if (Test-Path $target) {
        Write-Host "Usuwam istniejacy $($repo.dir)..."
        Remove-Item -Recurse -Force $target
    }
    Write-Host "Klonuje $($repo.name) -> $target"
    & git clone --depth 1 $repo.url $target
    if ($LASTEXITCODE -ne 0) { throw "git clone $($repo.name) nie powiodl sie." }
    # Pochodzenie zapisujemy PRZED usunieciem .git - inaczej kod trafiajacy do instalatora
    # jest nieidentyfikowalny i nie da sie powiedziec, ktora rewizje wydano.
    $commit = (& git -C $target rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "nie udalo sie odczytac commita dla $($repo.name)." }
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $provenance = @(
        "repository: $($repo.url)",
        "commit:     $commit",
        "fetched:    $stamp",
        "note:       plytki klon (--depth 1); katalog .git usuniety, zeby zmniejszyc bundel."
    ) -join "`r`n"
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText((Join-Path $target "SOURCE.txt"), $provenance + "`r`n", $encoding)

    # .git nie jest potrzebny do ladowania - usuwamy, zeby zmniejszyc rozmiar bundla.
    $gitDir = Join-Path $target ".git"
    if (Test-Path $gitDir) { Remove-Item -Recurse -Force $gitDir }
    Write-Host "OK: $($repo.dir)"
}

Write-Host ""
Write-Host "Gotowe. Kod repo DINO w: $Destination"
Write-Host "Wagi (*.pth) dostarcz osobno do tego samego katalogu (dinov2_*/dinov3_*)."
