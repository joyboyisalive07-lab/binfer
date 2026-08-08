<#
.SYNOPSIS
    Build dist/binfer.exe, a single console executable with no runtime deps.

.DESCRIPTION
    The entry point is src/binfer/__main__.py, the same module `python -m binfer`
    runs, so the frozen program and the source program take the same path into
    the CLI and cannot drift apart.

    The build is verified before it is called done: the executable has to run
    --self-test and recover the ground truth of every synthetic format, because
    a binary that starts and prints a version number has proved nothing.
#>
[CmdletBinding()]
param(
    # Skip the post-build verification. Only useful when cross-checking a build
    # on a machine that cannot execute the result.
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    foreach ($stale in 'build', 'dist', 'binfer.spec') {
        if (Test-Path $stale) {
            Remove-Item $stale -Recurse -Force
        }
    }

    Write-Host 'building dist/binfer.exe'
    python -m PyInstaller `
        --onefile `
        --console `
        --name binfer `
        --paths src `
        --noconfirm `
        --clean `
        --log-level WARN `
        src/binfer/__main__.py
    if ($LASTEXITCODE -ne 0) {
        throw "pyinstaller failed with exit code $LASTEXITCODE"
    }

    $exe = Join-Path $root 'dist/binfer.exe'
    if (-not (Test-Path $exe)) {
        throw "expected $exe to exist after the build"
    }
    $megabytes = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "built $exe ($megabytes MB)"

    if ($SkipVerify) {
        Write-Host 'verification skipped'
        return
    }

    Write-Host 'verifying the executable against the synthetic ground truth'
    & $exe --self-test --no-color
    if ($LASTEXITCODE -ne 0) {
        throw "the built executable failed its own self test (exit code $LASTEXITCODE)"
    }

    $reported = (& $exe --version)
    $declared = (python -c "import sys; sys.path.insert(0, 'src'); import binfer; print('binfer', binfer.__version__)")
    if ($reported -ne $declared) {
        throw "the executable reports '$reported' but the source says '$declared'"
    }
    Write-Host "verified: $reported"
}
finally {
    Pop-Location
}
