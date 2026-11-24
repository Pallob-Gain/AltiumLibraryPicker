param(
    [string]$GtkRoot = "C:\gtk",
    [string]$OutputDirectory = "dist\altium-library-manager",
    [string]$AppExe = "target\release\altium-library-manager.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([System.IO.Path]::IsPathRooted($AppExe)) {
    $executable = $AppExe
} else {
    $executable = Join-Path $projectRoot $AppExe
}
$gtkRootPath = (Resolve-Path -LiteralPath $GtkRoot).Path
if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $outputPath = $OutputDirectory
} else {
    $outputPath = Join-Path $projectRoot $OutputDirectory
}

if (Test-Path -LiteralPath $outputPath) {
    throw "Output directory already exists: $outputPath. Choose a new directory."
}
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Release executable not found: $executable. Run cargo build --release first."
}

$requiredPaths = @(
    "bin\gtk-4-1.dll",
    "bin\gio-2.0-0.dll",
    "lib\gdk-pixbuf-2.0",
    "lib\gdk-pixbuf-2.0\2.10.0\loaders.cache",
    "share\glib-2.0\schemas\gschemas.compiled",
    "share\icons",
    "share\gtk-4.0",
    "share\locale",
    "share\doc",
    "share\licenses",
    "etc\fonts"
)
foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath (Join-Path $gtkRootPath $path))) {
        throw "GTK runtime is missing $path in $gtkRootPath."
    }
}
$cache = Get-Content -LiteralPath (Join-Path $gtkRootPath "lib\gdk-pixbuf-2.0\2.10.0\loaders.cache")
if ($cache -match '^"\s*([A-Za-z]:|[/\\])') {
    throw "GTK image loader cache contains absolute paths and is not relocatable."
}

New-Item -ItemType Directory -Path $outputPath | Out-Null
Copy-Item -LiteralPath $executable -Destination (Join-Path $outputPath "altium-library-manager.exe")
Get-ChildItem -LiteralPath (Join-Path $gtkRootPath "bin") -Filter "*.dll" -File |
    Copy-Item -Destination $outputPath
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination $outputPath

foreach ($path in @("lib", "share", "etc")) {
    New-Item -ItemType Directory -Path (Join-Path $outputPath $path) | Out-Null
}
Copy-Item -LiteralPath (Join-Path $gtkRootPath "lib\gdk-pixbuf-2.0") -Destination (Join-Path $outputPath "lib") -Recurse
foreach ($path in @("glib-2.0\schemas", "icons", "gtk-4.0", "locale", "doc", "licenses")) {
    $destination = Join-Path (Join-Path $outputPath "share") (Split-Path -Parent $path)
    if (-not (Test-Path -LiteralPath $destination)) {
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
    }
    Copy-Item -LiteralPath (Join-Path $gtkRootPath "share\$path") -Destination $destination -Recurse
}
Copy-Item -LiteralPath (Join-Path $gtkRootPath "etc\fonts") -Destination (Join-Path $outputPath "etc") -Recurse
Write-Host "Windows bundle created at $outputPath. Zip the entire directory for distribution."
