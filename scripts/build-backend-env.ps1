param(
    [Parameter()]
    [string]$RepoRoot = "",

    [Parameter()]
    [string]$PythonVersion = "3.11",

    # analyst = torch CPU (dotychczasowe zachowanie, domyślne).
    # studio   = torch CUDA, potrzebny do treningu.
    [Parameter()]
    [ValidateSet("analyst", "studio")]
    [string]$Variant = "analyst",

    # Indeks kół CUDA. Wydzielony, bo zmienia się wraz z wersjami PyTorcha.
    [Parameter()]
    [string]$CudaIndexUrl = "https://download.pytorch.org/whl/cu126",

    # Katalog roboczy budowy. Przekierowanie pozwala zbudować runtime obok
    # istniejącego, bez kasowania środowiska używanego przez smoke testy.
    [Parameter()]
    [string]$BuildRoot = "",

    # Spike/pomiar: nie kopiuj archiwum do zasobów Tauri.
    [Parameter()]
    [switch]$SkipResourceCopy,

    # Rozmiar części archiwum w zasobach Tauri. Zarówno NSIS (mmap), jak i WiX
    # (light.exe) nie potrafią osadzić pojedynczego pliku ~2 GB+, a runtime CUDA ma
    # ponad 3 GB — dlatego archiwum trafia do zasobów pocięte na części.
    [Parameter()]
    [ValidateRange(64, 1900)]
    [int]$PartSizeMB = 1536
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
}

function Resolve-CondaCommand {
    $fromPath = Get-Command conda -ErrorAction SilentlyContinue
    if ($fromPath) {
        return $fromPath.Source
    }

    $candidates = @(
        "$env:USERPROFILE\anaconda3\Scripts\conda.exe",
        "$env:USERPROFILE\anaconda3\condabin\conda.bat",
        "$env:USERPROFILE\miniconda3\Scripts\conda.exe",
        "$env:USERPROFILE\miniconda3\condabin\conda.bat",
        "$env:LOCALAPPDATA\anaconda3\Scripts\conda.exe",
        "$env:LOCALAPPDATA\anaconda3\condabin\conda.bat",
        "$env:LOCALAPPDATA\miniconda3\Scripts\conda.exe",
        "$env:LOCALAPPDATA\miniconda3\condabin\conda.bat",
        "C:\ProgramData\anaconda3\Scripts\conda.exe",
        "C:\ProgramData\anaconda3\condabin\conda.bat",
        "C:\ProgramData\miniconda3\Scripts\conda.exe",
        "C:\ProgramData\miniconda3\condabin\conda.bat"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "conda was not found. Install Miniconda/Anaconda or add conda to PATH."
}

$condaCommand = Resolve-CondaCommand
Write-Host "Using conda: $condaCommand"

function Split-ArchiveIntoParts {
    <#
        Tnie archiwum na części `<nazwa>.NNN` w katalogu docelowym. Czyta strumieniowo,
        więc rozmiar pliku nie przekłada się na zużycie pamięci. Zwraca listę części.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$DestinationDir,
        [Parameter(Mandatory = $true)][string]$BaseName,
        [Parameter(Mandatory = $true)][long]$PartSizeBytes
    )

    $created = @()
    $input = [System.IO.File]::OpenRead($Path)
    try {
        $buffer = New-Object byte[] (8 * 1024 * 1024)
        $index = 1
        while ($input.Position -lt $input.Length) {
            $partPath = Join-Path $DestinationDir ("{0}.{1:D3}" -f $BaseName, $index)
            $output = [System.IO.File]::Create($partPath)
            try {
                [long]$written = 0
                while ($written -lt $PartSizeBytes) {
                    $toRead = [int][Math]::Min([long]$buffer.Length, $PartSizeBytes - $written)
                    $read = $input.Read($buffer, 0, $toRead)
                    if ($read -le 0) { break }
                    $output.Write($buffer, 0, $read)
                    $written += $read
                }
            } finally {
                $output.Dispose()
            }
            $created += $partPath
            $index++
        }
    } finally {
        $input.Dispose()
    }
    return $created
}

function Invoke-Checked {
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

if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = Join-Path $RepoRoot ".desktop-build"
}
Write-Host "Variant: $Variant"
Write-Host "Build root: $BuildRoot"

$envRoot = Join-Path $BuildRoot "backend-env"
$packedArchive = Join-Path $BuildRoot "backend-env.tar.gz"
$stagedPackedArchive = Join-Path $BuildRoot "backend-env.new.tar.gz"
$resourceEnv = Join-Path $RepoRoot "frontend\src-tauri\resources\backend-env"
$resourceArchive = Join-Path $RepoRoot "frontend\src-tauri\resources\backend-env.tar.gz"
$stagedResourceArchive = Join-Path $RepoRoot "frontend\src-tauri\resources\backend-env.new.tar.gz"

if (Test-Path $envRoot) {
    Remove-Item $envRoot -Recurse -Force
}
Remove-Item $stagedPackedArchive -Force -ErrorAction SilentlyContinue
if (-not $SkipResourceCopy) {
    Remove-Item $stagedResourceArchive -Force -ErrorAction SilentlyContinue
    if (Test-Path $resourceEnv) {
        Get-ChildItem $resourceEnv -Force |
            Where-Object { $_.Name -ne ".gitkeep" } |
            Remove-Item -Recurse -Force
    } else {
        New-Item -ItemType Directory -Path $resourceEnv -Force | Out-Null
    }
}

New-Item -ItemType Directory -Path (Split-Path -Parent $envRoot) -Force | Out-Null

try {
    Invoke-Checked -FilePath $condaCommand -Arguments @("create", "-y", "-p", $envRoot, "python=$PythonVersion")
    Invoke-Checked -FilePath $condaCommand -Arguments @("install", "-y", "-p", $envRoot, "-c", "conda-forge", "rasterio", "gdal", "numpy", "pillow", "pyyaml", "pyarrow", "conda-pack")
    # Install the JP2 driver explicitly. Keeping it in the broad transaction can
    # let conda satisfy GDAL without materializing the optional plugin package.
    Invoke-Checked -FilePath $condaCommand -Arguments @("install", "-y", "-p", $envRoot, "-c", "conda-forge", "libgdal-jp2openjpeg")
    Invoke-Checked -FilePath $condaCommand -Arguments @(
        "run", "-p", $envRoot, "python", "-m", "pip", "install",
        "fastapi",
        "uvicorn[standard]",
        "python-multipart",
        "pydantic>=2.0",
        "opencv-python-headless",
        "sse-starlette>=2.0",
        "psutil>=5.9",
        "usearch>=2.23,<3"
    )

    # Stos ML jest bundlowany w obu wariantach — różni je wyłącznie koło torcha.
    # analyst: CPU (SAM, egzemplarz, predykcja). studio: CUDA (dodatkowo trening).
    $torchIndexUrl = if ($Variant -eq "studio") { $CudaIndexUrl } else { "https://download.pytorch.org/whl/cpu" }
    Write-Host "Torch index: $torchIndexUrl"
    Invoke-Checked -FilePath $condaCommand -Arguments @(
        "run", "-p", $envRoot, "python", "-m", "pip", "install",
        "torch",
        "torchvision",
        "--index-url", $torchIndexUrl
    )
    Invoke-Checked -FilePath $condaCommand -Arguments @(
        "run", "-p", $envRoot, "python", "-m", "pip", "install",
        "ultralytics>=8.0"
    )
    # SAM3 uzywa tekstowego encodera opartego o CLIP (fork ultralytics) + ftfy/regex, a jego
    # backbone obrazowy wymaga `timm`. Bez tych pakietow ultralytics probuje doinstalowac je
    # z sieci w trakcie uzycia (pip w handlerze → zawis/"Network error" offline). Bundlujemy je
    # tutaj, aby SAM3 (klik multimask i tryb tekstowy) dzialal od razu. Zweryfikowane sprzetowo:
    # scripts/verify-sam-real.py. (torch.compile SAM3 wylaczany runtime przez TORCHDYNAMO_DISABLE.)
    Invoke-Checked -FilePath $condaCommand -Arguments @(
        "run", "-p", $envRoot, "python", "-m", "pip", "install",
        "ftfy",
        "regex",
        "timm",
        "git+https://github.com/ultralytics/CLIP.git"
    )

    $runtimePython = Join-Path $envRoot "python.exe"
    $gdalPluginDir = Join-Path $envRoot "Library\lib\gdalplugins"
    $previousGdalDriverPath = $env:GDAL_DRIVER_PATH
    $previousPath = $env:PATH
    try {
        $env:GDAL_DRIVER_PATH = $gdalPluginDir
        $env:PATH = "$(Join-Path $envRoot 'Library\bin');$envRoot;$previousPath"
        Invoke-Checked -FilePath $runtimePython -Arguments @(
            "-c",
            "from osgeo import gdal; import os,tempfile,rasterio; gdal.UseExceptions(); assert 'JP2OpenJPEG' in rasterio.Env().__enter__().drivers(), 'JP2OpenJPEG driver is unavailable'; p=os.path.join(tempfile.gettempdir(),'geotile-jp2-driver-test.jp2'); s=gdal.GetDriverByName('MEM').Create('',8,8,1,gdal.GDT_Byte); d=gdal.GetDriverByName('JP2OpenJPEG').CreateCopy(p,s); assert d is not None, 'JP2OpenJPEG cannot create a raster'; d=None; s=None; r=rasterio.open(p); assert (r.width,r.height,r.count)==(8,8,1); r.close(); os.remove(p)"
        )
        # NITF driver — wymagany dla lotniczych scen sensorowych (DESIGN_DECISIONS.md, nitf).
        Invoke-Checked -FilePath $runtimePython -Arguments @(
            "-c",
            "from osgeo import gdal; import os,tempfile; gdal.UseExceptions(); assert gdal.GetDriverByName('NITF') is not None, 'NITF driver is unavailable'; p=os.path.join(tempfile.gettempdir(),'geotile-nitf-driver-test.ntf'); s=gdal.GetDriverByName('MEM').Create('',8,8,1,gdal.GDT_UInt16); d=gdal.GetDriverByName('NITF').CreateCopy(p,s); assert d is not None, 'NITF cannot create a raster'; d=None; s=None; r=gdal.Open(p); assert (r.RasterXSize,r.RasterYSize,r.GetRasterBand(1).DataType)==(8,8,gdal.GDT_UInt16); r=None; os.remove(p)"
        )
    } finally {
        $env:GDAL_DRIVER_PATH = $previousGdalDriverPath
        $env:PATH = $previousPath
    }

    # --- Prune ścieżek przekraczających MAX_PATH (260) na stacji docelowej -----------
    # torch wendoruje licencje kilka poziomów w głąb (np. torch-*.dist-info\licenses\
    # third_party\...\LICENSE.txt). Przy dłuższej nazwie użytkownika pełna ścieżka po
    # instalacji do %APPDATA%\Roaming przekracza 260 znaków, a conda-unpack sklejaścieżkę
    # z ukośnikami w przód (`//?/`), przez co prefiks długich ścieżek `\\?\` NIE działa i
    # unpack pada z FileNotFoundError. Te pliki to czysta metadana licencyjna — nie są
    # importowane ani nie zawierają placeholdera prefiksu, więc usunięcie ich przed
    # spakowaniem jest bezpieczne i skraca najdłuższą ścieżkę o ~150 znaków. Górna licencja
    # torcha (dist-info\LICENSE / METADATA) zostaje nietknięta.
    Write-Host "Pruning deeply-nested vendored license trees (MAX_PATH safety)..."
    $prunedCount = 0
    Get-ChildItem -LiteralPath $envRoot -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "licenses" -and $_.FullName -match "\.dist-info\\licenses$" } |
        ForEach-Object {
            $thirdParty = Join-Path $_.FullName "third_party"
            if (Test-Path -LiteralPath $thirdParty) {
                # Robocopy z pustego katalogu radzi sobie z >260-znakowymi ścieżkami przy
                # kasowaniu (mirror pustki), inaczej Remove-Item potyka się o MAX_PATH.
                $empty = Join-Path $env:TEMP ("gtl-empty-" + [Guid]::NewGuid().ToString("N"))
                New-Item -ItemType Directory -Path $empty -Force | Out-Null
                & robocopy $empty $thirdParty /MIR /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
                Remove-Item -LiteralPath $thirdParty -Recurse -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath $empty -Recurse -Force -ErrorAction SilentlyContinue
                $prunedCount++
            }
        }
    Write-Host ("  Pruned {0} vendored third_party license tree(s)" -f $prunedCount)

    # --- Zapis skladu srodowiska ------------------------------------------------
    # Ta budowa instaluje najnowsze zgodne wersje, wiec bez tego kroku nic w repo
    # nie mowi, co uzytkownik faktycznie dostal. Plik wchodzi do wydania.
    & (Join-Path $PSScriptRoot "write-runtime-lock.ps1") `
        -EnvPath $envRoot -Variant $Variant -CondaCommand $condaCommand -RepoRoot $RepoRoot
    if ($LASTEXITCODE -ne 0) { throw "write-runtime-lock.ps1 zakonczyl sie kodem $LASTEXITCODE" }

    Invoke-Checked -FilePath $condaCommand -Arguments @(
        "run", "-p", $envRoot, "conda-pack",
        "-p", $envRoot,
        "-o", $stagedPackedArchive,
        "--force",
        "--ignore-missing-files"
    )

    Move-Item $stagedPackedArchive $packedArchive -Force
    if (-not $SkipResourceCopy) {
        # Zasoby Tauri dostają archiwum pocięte na części; całość zostaje w
        # .desktop-build jako artefakt referencyjny (testy, pomiary).
        $resourceDir = Split-Path -Parent $resourceArchive
        Remove-Item $resourceArchive -Force -ErrorAction SilentlyContinue
        Get-ChildItem $resourceDir -Filter "backend-env.tar.gz.*" -File -ErrorAction SilentlyContinue |
            Remove-Item -Force
        $parts = Split-ArchiveIntoParts -Path $packedArchive -DestinationDir $resourceDir `
            -BaseName "backend-env.tar.gz" -PartSizeBytes ([long]$PartSizeMB * 1MB)
        Write-Host ("Archiwum pociete na {0} czesci po max {1} MB" -f $parts.Count, $PartSizeMB)
        foreach ($part in $parts) {
            Write-Host ("  {0}  ({1:N2} GB)" -f (Split-Path $part -Leaf), ((Get-Item $part).Length / 1GB))
        }
    }
} catch {
    throw "Backend runtime build failed. The build requires working Internet access and DNS for repo.anaconda.com, conda-forge, PyPI and download.pytorch.org. Existing runtime archives were preserved. Details: $($_.Exception.Message)"
} finally {
    Remove-Item $stagedPackedArchive -Force -ErrorAction SilentlyContinue
    Remove-Item $stagedResourceArchive -Force -ErrorAction SilentlyContinue
}

if ($SkipResourceCopy) {
    Write-Host "Backend conda runtime archive prepared (spike, resources untouched): $packedArchive"
} else {
    Write-Host "Backend conda runtime archive prepared for Tauri resources: $resourceArchive"
}

# --- Pomiar dla M0a -------------------------------------------------------
$envSizeBytes = (Get-ChildItem $envRoot -Recurse -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum
$archiveBytes = (Get-Item $packedArchive -ErrorAction SilentlyContinue).Length
Write-Host ""
Write-Host "=== Pomiar runtime ($Variant) ==="
Write-Host ("Rozpakowane srodowisko : {0:N2} GB" -f ($envSizeBytes / 1GB))
Write-Host ("Archiwum conda-pack    : {0:N2} GB" -f ($archiveBytes / 1GB))
