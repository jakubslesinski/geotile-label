<#
    Zapisz doklandy sklad srodowiska backendu, ktore trafia do instalatora.

    PO CO. `environment.yml` opisuje srodowisko DEWELOPERSKIE i jest przypiete recznie.
    Runtime pakowany do instalatora buduje `build-backend-env.ps1`, ktory instaluje
    najnowsze zgodne wersje - wiec to, co dostaje uzytkownik, nie wynika z zadnego
    wersjonowanego pliku. Ten skrypt domyka luke: po zbudowaniu srodowiska zapisuje jego
    faktyczny sklad, a plik wchodzi do repozytorium razem z wydaniem.

    DLACZEGO NIE SAM `pip freeze`. Srodowisko jest hybrydowe. Stos geoprzestrzenny
    (GDAL, PROJ, rasterio) instaluje conda i `pip freeze` go NIE widzi - a to wlasnie ta
    czesc decyduje o odtwarzalnosci odczytu rastrow. Zapisujemy obie polowy:
    `conda list --explicit` (pelne URL-e, gotowe do `conda create --file`) oraz wersje
    pakietow zainstalowanych pipem.

    UZYCIE
        .\scripts\write-runtime-lock.ps1 -EnvPath .desktop-build\backend-env
        .\scripts\write-runtime-lock.ps1 -EnvPath <env> -Variant studio -Output runtime-lock-studio.txt
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$EnvPath,

    [Parameter()]
    [string]$Output = "",

    # Wariant runtime'u: analyst (torch CPU) albo studio (torch CUDA).
    [Parameter()]
    [string]$Variant = "analyst",

    [Parameter()]
    [string]$CondaCommand = "conda",

    [Parameter()]
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($Output)) {
    $Output = Join-Path $RepoRoot "runtime-lock.txt"
}
$EnvPath = [System.IO.Path]::GetFullPath($EnvPath)
if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "Srodowisko nie istnieje: $EnvPath"
}

Write-Host "Zapisuje sklad runtime'u: $EnvPath"

# --- polowa condy: pelne URL-e, odtwarzalne przez `conda create --file` -----------------
$explicit = & $CondaCommand list -p $EnvPath --explicit
if ($LASTEXITCODE -ne 0) { throw "conda list --explicit zakonczylo sie kodem $LASTEXITCODE" }
$condaLines = $explicit | Where-Object { $_ -match "^https?://" }

# --- polowa pipa: `conda list` oznacza je kanalem `pypi` --------------------------------
$listing = & $CondaCommand list -p $EnvPath
if ($LASTEXITCODE -ne 0) { throw "conda list zakonczylo sie kodem $LASTEXITCODE" }
$pipLines = @()
foreach ($line in $listing) {
    if ($line -match "^\s*#") { continue }
    $parts = ($line -split "\s+") | Where-Object { $_ -ne "" }
    if ($parts.Count -ge 4 -and $parts[3] -eq "pypi") {
        $pipLines += ("{0}=={1}" -f $parts[0], $parts[1])
    }
}

$pythonExe = Join-Path $EnvPath "python.exe"
$pythonVersion = if (Test-Path -LiteralPath $pythonExe) {
    (& $pythonExe -c "import platform; print(platform.python_version())")
} else { "nieznana" }

$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

$header = @"
# Sklad runtime'u backendu spakowanego do instalatora GeoTile Label.
#
# Wygenerowane przez scripts/write-runtime-lock.ps1 przy budowie srodowiska.
# To jest zapis tego, CO SIE FAKTYCZNIE WYSYLA - w odroznieniu od environment.yml,
# ktory opisuje srodowisko deweloperskie i nie steruje ta budowa.
#
#   wariant       : $Variant
#   python        : $pythonVersion
#   pakietow conda: $($condaLines.Count)
#   pakietow pip  : $($pipLines.Count)
#   zapisano      : $stamp
#
# ODTWORZENIE
#   1. conda create -p <docelowy-env> --file <ta sekcja conda>
#   2. <docelowy-env>\python.exe -m pip install -r <ta sekcja pip>
#
# Sekcje sa rozdzielone znacznikami nizej; obie sa potrzebne. Sam pip nie wystarczy,
# bo stos geoprzestrzenny (GDAL, PROJ, rasterio) pochodzi z condy.
#
# UWAGA o torchu: kolo torcha pobierane jest z indeksu PyTorcha, nie z PyPI
# (analyst: https://download.pytorch.org/whl/cpu). Przy odtwarzaniu podaj ten sam
# --index-url, inaczej pip zainstaluje inny wariant.

# ===== CONDA (conda list --explicit) =====
@EXPLICIT
"@

$sections = New-Object System.Collections.Generic.List[string]
$sections.Add($header)
foreach ($line in $condaLines) { $sections.Add($line) }
$sections.Add("")
$sections.Add("# ===== PIP (pakiety z kanalu pypi) =====")
foreach ($line in ($pipLines | Sort-Object)) { $sections.Add($line) }
$sections.Add("")

$encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($Output, ($sections -join "`r`n"), $encoding)

Write-Host ("Zapisano {0}: {1} pakietow conda, {2} pip" -f (Split-Path $Output -Leaf), $condaLines.Count, $pipLines.Count)
