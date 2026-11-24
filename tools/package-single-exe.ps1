param(
    [string]$GtkRoot = "C:\gtk",
    [string]$VcRuntimeDir,
    [string]$AppExe = "target\release\altium-library-manager.exe",
    [string]$OutputFile = "dist\AltiumLibraryManager.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([System.IO.Path]::IsPathRooted($OutputFile)) {
    $outputPath = $OutputFile
} else {
    $outputPath = Join-Path $projectRoot $OutputFile
}
if (Test-Path -LiteralPath $outputPath) {
    throw "Output file already exists: $outputPath. Choose a new file."
}

if (-not $VcRuntimeDir) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        throw "Visual Studio redistributable not found. Specify -VcRuntimeDir."
    }
    $vsPath = & $vswhere -latest -products '*' -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $vsPath) {
        throw "Visual Studio installation not found. Specify -VcRuntimeDir."
    }
    $redistRoot = Join-Path $vsPath "VC\Redist\MSVC"
    if (-not (Test-Path -LiteralPath $redistRoot -PathType Container)) {
        throw "Visual C++ redistributable not found. Specify -VcRuntimeDir."
    }
    $candidates = @(Get-ChildItem -LiteralPath $redistRoot -Directory |
        Where-Object { $_.Name -match '^\d+(\.\d+)+$' } |
        Sort-Object { [version]$_.Name } -Descending)
    foreach ($candidate in $candidates) {
        $crtDir = Join-Path $candidate.FullName "x64\Microsoft.VC143.CRT"
        if (Test-Path -LiteralPath $crtDir -PathType Container) {
            $VcRuntimeDir = $crtDir
            break
        }
    }
    if (-not $VcRuntimeDir) {
        throw "Visual C++ x64 redistributable not found. Specify -VcRuntimeDir."
    }
}
$vcPath = (Resolve-Path -LiteralPath $VcRuntimeDir).Path
$crtDlls = @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
foreach ($dll in $crtDlls) {
    if (-not (Test-Path -LiteralPath (Join-Path $vcPath $dll) -PathType Leaf)) {
        throw "Visual C++ redistributable is missing $dll in $vcPath."
    }
}

function Assert-X64Binary([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $reader = New-Object System.IO.BinaryReader($stream)
        if ($reader.ReadUInt16() -ne 0x5A4D) {
            throw "Not a Windows executable: $Path"
        }
        $stream.Position = 0x3c
        $stream.Position = $reader.ReadUInt32()
        if ($reader.ReadUInt32() -ne 0x4550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw "Expected an x64 Windows executable: $Path"
        }
    } finally {
        $stream.Dispose()
    }
}

function Assert-CompatibleRuntime([string]$Path, [version]$RuntimeVersion) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $reader = New-Object System.IO.BinaryReader($stream)
        $stream.Position = 0x3c
        $stream.Position = $reader.ReadUInt32() + 26
        $major = $reader.ReadByte()
        $minor = $reader.ReadByte()
        if ($major -eq 14 -and [version]"$major.$minor" -gt $RuntimeVersion) {
            throw "$Path was linked with MSVC $major.$minor, newer than the Visual C++ redistributable $RuntimeVersion. Supply a newer -VcRuntimeDir or rebuild with a compatible toolset."
        }
    } finally {
        $stream.Dispose()
    }
}

$runtimeVersion = [version](Get-Item -LiteralPath (Join-Path $vcPath "vcruntime140.dll")).VersionInfo.FileVersion
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("alm-package-" + [guid]::NewGuid().ToString("N"))
$previousArchive = $env:ALM_RUNTIME_ARCHIVE
$previousFlags = $env:RUSTFLAGS
New-Item -ItemType Directory -Path $tempRoot | Out-Null
try {
    $bundle = Join-Path $tempRoot "bundle"
    & (Join-Path $PSScriptRoot "package-windows.ps1") -GtkRoot $GtkRoot -OutputDirectory $bundle -AppExe $AppExe
    foreach ($dll in $crtDlls) {
        Copy-Item -LiteralPath (Join-Path $vcPath $dll) -Destination $bundle
    }
    Get-ChildItem -LiteralPath $bundle -Recurse -File |
        Where-Object { $_.Extension -in @(".exe", ".dll") } |
        ForEach-Object {
            Assert-X64Binary $_.FullName
            Assert-CompatibleRuntime $_.FullName $runtimeVersion
        }

    $archive = Join-Path $tempRoot "runtime.zip"
    Compress-Archive -Path (Join-Path $bundle "*") -DestinationPath $archive
    $env:ALM_RUNTIME_ARCHIVE = $archive
    $env:RUSTFLAGS = "$previousFlags -C target-feature=+crt-static"
    $targetDir = Join-Path $PSScriptRoot "launcher\target"
    & cargo build --release --locked --target x86_64-pc-windows-msvc --target-dir $targetDir --manifest-path (Join-Path $PSScriptRoot "launcher\Cargo.toml")
    if ($LASTEXITCODE -ne 0) {
        throw "Building the single-file launcher failed."
    }
    $launcher = Join-Path $PSScriptRoot "launcher\target\x86_64-pc-windows-msvc\release\altium-library-manager-launcher.exe"
    Assert-X64Binary $launcher
    $outputParent = Split-Path -Parent $outputPath
    if (-not (Test-Path -LiteralPath $outputParent)) {
        New-Item -ItemType Directory -Path $outputParent -Force | Out-Null
    }
    [System.IO.File]::Copy($launcher, $outputPath, $false)
    Write-Host "Single-file Windows release created at $outputPath."
} finally {
    $env:ALM_RUNTIME_ARCHIVE = $previousArchive
    $env:RUSTFLAGS = $previousFlags
    Remove-Item -LiteralPath $tempRoot -Recurse -Force
}
