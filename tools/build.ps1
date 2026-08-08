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
    foreach ($stale in 'build', 'dist', 'binfer.spec', 'version_info.txt') {
        if (Test-Path $stale) {
            Remove-Item $stale -Recurse -Force
        }
    }

    $version = (python -c "import sys; sys.path.insert(0, 'src'); import binfer; print(binfer.__version__)")
    if ($LASTEXITCODE -ne 0) { throw 'could not read the version from the source' }
    $parts = $version.Split('.')
    $quad = "$($parts[0]), $($parts[1]), $($parts[2]), 0"

    # An unsigned executable with no version resource is anonymous to
    # SmartScreen and to anyone reading its properties. The resource does not
    # replace a signature, but it costs nothing and it is what the reputation
    # heuristics look at first.
    $versionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($quad), prodvers=($quad),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'joyboyisalive07-lab'),
      StringStruct('FileDescription', 'binfer - structure inference for unknown binary formats'),
      StringStruct('FileVersion', '$version.0'),
      StringStruct('InternalName', 'binfer'),
      StringStruct('LegalCopyright', 'MIT License. Copyright (c) 2026 joyboyisalive07-lab'),
      StringStruct('OriginalFilename', 'binfer.exe'),
      StringStruct('ProductName', 'binfer'),
      StringStruct('ProductVersion', '$version')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    Set-Content -Path 'version_info.txt' -Value $versionInfo -Encoding ascii

    # Nothing is excluded from the bundle. Trimming the modules binfer does not
    # import looked worth about 1.4 MB, but on Python 3.12 pathlib imports
    # urllib.parse and the interpreter would not start; 3.14 has no such import,
    # so it passed locally and failed on the version releases are built with.
    # A size saving that depends on the interpreter version is not worth the
    # risk of shipping an executable that cannot start.
    Write-Host "building dist/binfer.exe for version $version"
    python -m PyInstaller `
        --onefile `
        --console `
        --name binfer `
        --paths src `
        --noconfirm `
        --clean `
        --log-level WARN `
        --version-file version_info.txt `
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

    # Published beside the executable so a download can be checked without
    # trusting the transfer. It is not a signature, but it is verifiable.
    $digest = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
    Set-Content -Path (Join-Path $root 'dist/binfer.exe.sha256') `
        -Value "$digest *binfer.exe" -Encoding ascii
    Write-Host "sha256 $digest"

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
