param(
    [string]$ProjectId = "",
    [string]$ScenePath = "",
    [string]$ModelPath = "",
    [ValidateSet("auto", "cpu", "cuda")]
    [string]$InferenceDevice = "auto",
    [string]$DataDir = "",
    [string]$PythonPath = "",
    [string]$OutputRoot = "",
    [string]$StorageProfile = "unspecified",
    [int]$LegacySceneSample = 3,
    [int]$SyntheticTileCount = 64,
    [int]$SyntheticTileSize = 256,
    [int]$SyntheticEmbeddingObjects = 5000,
    [int]$SyntheticEmbeddingDimensions = 384
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PackedPython = Join-Path $RepoRoot ".desktop-build\backend-env\python.exe"
    $VenvPython = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $PackedPython) {
        $PythonPath = $PackedPython
    } elseif (Test-Path -LiteralPath $VenvPython) {
        $PythonPath = $VenvPython
    } else {
        $PythonPath = "python"
    }
}

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    $DataDir = Join-Path $env:APPDATA "GeoTileLabel\data"
}
if ([string]::IsNullOrWhiteSpace($ScenePath) -xor [string]::IsNullOrWhiteSpace($ModelPath)) {
    throw "ScenePath and ModelPath must be supplied together."
}

$Timestamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $RepoRoot "benchmark-results"
}
$RunDirectory = Join-Path $OutputRoot $Timestamp
New-Item -ItemType Directory -Path $RunDirectory -Force | Out-Null

$PythonResolved = if (Test-Path -LiteralPath $PythonPath) {
    (Resolve-Path -LiteralPath $PythonPath).Path
} else {
    $PythonPath
}

# Packed Conda runtimes require these paths when launched directly, outside Tauri/conda run.
$RuntimeRoot = if (Test-Path -LiteralPath $PythonResolved) {
    Split-Path -Parent $PythonResolved
} else {
    ""
}
$SavedEnvironment = @{
    PATH = $env:PATH
    DATA_DIR = $env:DATA_DIR
    GDAL_DATA = $env:GDAL_DATA
    PROJ_LIB = $env:PROJ_LIB
    GDAL_DRIVER_PATH = $env:GDAL_DRIVER_PATH
    GEOTILE_BENCHMARK_READ_ONLY = $env:GEOTILE_BENCHMARK_READ_ONLY
    OMP_NUM_THREADS = $env:OMP_NUM_THREADS
    MKL_NUM_THREADS = $env:MKL_NUM_THREADS
    OPENBLAS_NUM_THREADS = $env:OPENBLAS_NUM_THREADS
    NUMEXPR_NUM_THREADS = $env:NUMEXPR_NUM_THREADS
    KMP_DUPLICATE_LIB_OK = $env:KMP_DUPLICATE_LIB_OK
}

$Results = [System.Collections.Generic.List[object]]::new()
$Failed = 0

function Invoke-PerformanceBenchmark {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Script,
        [Parameter(Mandatory = $true)][string]$OutputFile,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    Write-Host "[$Name]"
    $Started = [DateTime]::UtcNow
    $Status = "completed"
    $ErrorMessage = $null
    try {
        & $PythonResolved -B $Script @Arguments --output $OutputFile
        if ($LASTEXITCODE -ne 0) {
            throw "Benchmark exited with code $LASTEXITCODE"
        }
        if (-not (Test-Path -LiteralPath $OutputFile)) {
            throw "Benchmark did not create $OutputFile"
        }
        $Report = Get-Content -Raw -Encoding UTF8 -LiteralPath $OutputFile | ConvertFrom-Json
        if ($Report.schema_name -ne "geotile_performance_report" -or $Report.status -ne "completed") {
            throw "Invalid or failed performance report"
        }
    } catch {
        $Status = "failed"
        $ErrorMessage = $_.Exception.Message
        $script:Failed++
        Write-Warning "$Name failed: $ErrorMessage"
    }
    $Results.Add([ordered]@{
        name = $Name
        status = $Status
        output = $OutputFile
        started_at = $Started.ToString("o")
        completed_at = [DateTime]::UtcNow.ToString("o")
        error = $ErrorMessage
    })
}

try {
    if ($RuntimeRoot -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot "Library\bin"))) {
        $env:PATH = "$(Join-Path $RuntimeRoot 'Library\bin');$RuntimeRoot;$(Join-Path $RuntimeRoot 'DLLs');$($env:PATH)"
        $env:GDAL_DATA = Join-Path $RuntimeRoot "Library\share\gdal"
        $env:PROJ_LIB = Join-Path $RuntimeRoot "Library\share\proj"
        $env:GDAL_DRIVER_PATH = Join-Path $RuntimeRoot "Library\lib\gdalplugins"
    }
    $env:DATA_DIR = $DataDir
    $env:GEOTILE_BENCHMARK_READ_ONLY = "1"
    $env:OMP_NUM_THREADS = "1"
    $env:MKL_NUM_THREADS = "1"
    $env:OPENBLAS_NUM_THREADS = "1"
    $env:NUMEXPR_NUM_THREADS = "1"
    $env:KMP_DUPLICATE_LIB_OK = "TRUE"

    $DatasetScript = Join-Path $RepoRoot "backend\benchmarks\benchmark_dataset_build.py"
    Invoke-PerformanceBenchmark `
        -Name "dataset-synthetic" `
        -Script $DatasetScript `
        -OutputFile (Join-Path $RunDirectory "dataset_synthetic.json") `
        -Arguments @(
            "--tile-count", "$SyntheticTileCount",
            "--tile-size", "$SyntheticTileSize",
            "--cache-state", "cold",
            "--storage-profile", $StorageProfile
        )

    $EmbeddingScript = Join-Path $RepoRoot "backend\benchmarks\benchmark_embedding_search.py"
    Invoke-PerformanceBenchmark `
        -Name "embedding-search-synthetic" `
        -Script $EmbeddingScript `
        -OutputFile (Join-Path $RunDirectory "embedding_search_synthetic.json") `
        -Arguments @(
            "--objects", "$SyntheticEmbeddingObjects",
            "--dimensions", "$SyntheticEmbeddingDimensions",
            "--cache-state", "cold",
            "--storage-profile", $StorageProfile
        )

    if (-not [string]::IsNullOrWhiteSpace($ProjectId)) {
        $CatalogScript = Join-Path $RepoRoot "backend\benchmarks\benchmark_catalog.py"
        $CatalogSnapshotScript = Join-Path $RepoRoot "backend\benchmarks\benchmark_catalog_snapshot.py"
        $SummaryScript = Join-Path $RepoRoot "backend\benchmarks\benchmark_project_summary.py"
        foreach ($CacheState in @("cold", "warm")) {
            Invoke-PerformanceBenchmark `
                -Name "catalog-$CacheState" `
                -Script $CatalogScript `
                -OutputFile (Join-Path $RunDirectory "catalog_$CacheState.json") `
                -Arguments @(
                    "--project-id", $ProjectId,
                    "--data-dir", $DataDir,
                    "--cache-state", $CacheState,
                    "--storage-profile", $StorageProfile,
                    "--legacy-scene-sample", "$LegacySceneSample"
                )
            Invoke-PerformanceBenchmark `
                -Name "project-summary-$CacheState" `
                -Script $SummaryScript `
                -OutputFile (Join-Path $RunDirectory "project_summary_$CacheState.json") `
                -Arguments @(
                    "--project-id", $ProjectId,
                    "--data-dir", $DataDir,
                    "--cache-state", $CacheState,
                    "--storage-profile", $StorageProfile
                )
            Invoke-PerformanceBenchmark `
                -Name "catalog-snapshot-$CacheState" `
                -Script $CatalogSnapshotScript `
                -OutputFile (Join-Path $RunDirectory "catalog_snapshot_$CacheState.json") `
                -Arguments @(
                    "--project-id", $ProjectId,
                    "--data-dir", $DataDir,
                    "--cache-state", $CacheState,
                    "--storage-profile", $StorageProfile
                )
        }
    } else {
        Write-Host "ProjectId not supplied: catalog and project-summary benchmarks skipped."
    }

    if (-not [string]::IsNullOrWhiteSpace($ScenePath)) {
        $InferenceScript = Join-Path $RepoRoot "backend\benchmarks\benchmark_inference.py"
        Invoke-PerformanceBenchmark `
            -Name "whole-scene-inference" `
            -Script $InferenceScript `
            -OutputFile (Join-Path $RunDirectory "whole_scene_inference.json") `
            -Arguments @(
                "--scene-path", $ScenePath,
                "--model-path", $ModelPath,
                "--device", $InferenceDevice,
                "--cache-state", "cold",
                "--storage-profile", $StorageProfile
            )
    } else {
        Write-Host "ScenePath/ModelPath not supplied: inference benchmark skipped."
    }

    $Manifest = [ordered]@{
        schema_name = "geotile_performance_baseline_manifest"
        schema_version = 1
        created_at = [DateTime]::UtcNow.ToString("o")
        repository = $RepoRoot
        python = $PythonResolved
        data_dir = $DataDir
        project_id = if ($ProjectId) { $ProjectId } else { $null }
        storage_profile = $StorageProfile
        result_count = $Results.Count
        failed_count = $Failed
        results = $Results
    }
    $ManifestPath = Join-Path $RunDirectory "baseline_manifest.json"
    [System.IO.File]::WriteAllText(
        $ManifestPath,
        ($Manifest | ConvertTo-Json -Depth 10),
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "Baseline manifest: $ManifestPath"
} finally {
    $env:PATH = $SavedEnvironment.PATH
    $env:DATA_DIR = $SavedEnvironment.DATA_DIR
    $env:GDAL_DATA = $SavedEnvironment.GDAL_DATA
    $env:PROJ_LIB = $SavedEnvironment.PROJ_LIB
    $env:GDAL_DRIVER_PATH = $SavedEnvironment.GDAL_DRIVER_PATH
    $env:GEOTILE_BENCHMARK_READ_ONLY = $SavedEnvironment.GEOTILE_BENCHMARK_READ_ONLY
    $env:OMP_NUM_THREADS = $SavedEnvironment.OMP_NUM_THREADS
    $env:MKL_NUM_THREADS = $SavedEnvironment.MKL_NUM_THREADS
    $env:OPENBLAS_NUM_THREADS = $SavedEnvironment.OPENBLAS_NUM_THREADS
    $env:NUMEXPR_NUM_THREADS = $SavedEnvironment.NUMEXPR_NUM_THREADS
    $env:KMP_DUPLICATE_LIB_OK = $SavedEnvironment.KMP_DUPLICATE_LIB_OK
}

if ($Failed -gt 0) {
    throw "$Failed performance benchmark(s) failed. See $RunDirectory."
}
