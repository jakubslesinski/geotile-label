param(
    [Parameter()]
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Split-Path -Parent $PSScriptRoot
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

& (Join-Path $PSScriptRoot "prepare-tauri-backend.ps1") -RepoRoot $RepoRoot
& (Join-Path $PSScriptRoot "build-backend-env.ps1") -RepoRoot $RepoRoot

Push-Location (Join-Path $RepoRoot "frontend")
try {
    Invoke-Checked "npm.cmd" run tauri:build
} finally {
    Pop-Location
}
