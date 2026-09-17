# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    desktop-pet onedir build + portable zip packaging.

.DESCRIPTION
    Builds a PyInstaller --onedir variant (no runtime extraction, no _MEI cache),
    output at dist-onedir\<name>\ plus a <name>-portable.zip green package.

    Variants:
      desktop-pet - Full WebM assets + AI companion (default)
      webm-chat   - WebM assets + AI chat
      webm        - WebM assets, no chat
      gif-chat    - GIF assets + AI chat (run with -Gif to generate GIFs first)
      gif         - GIF assets, no chat
#>
param(
    [string]$Variant = 'desktop-pet',
    [switch]$SkipBuild,
    [switch]$SkipZip,
    [switch]$SkipCheck,
    [switch]$Gif
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$py = if ($env:DESKTOP_PET_PYTHON -and (Test-Path -LiteralPath $env:DESKTOP_PET_PYTHON)) {
    $env:DESKTOP_PET_PYTHON
} elseif (Test-Path 'D:\python\miniconda\envs\py12\python.exe') {
    'D:\python\miniconda\envs\py12\python.exe'
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    (Get-Command python).Source
} else {
    throw "Python executable not found"
}
$pythonRoot = Split-Path -Parent $py

$variants = @{
    'desktop-pet' = @{ Name = 'desktop-pet'; Entry = 'packaging\pet_entry.py' }
    'webm-chat'   = @{ Name = 'desktop-pet-webm-chat'; Entry = 'packaging\pet_entry.py' }
    'webm'        = @{ Name = 'desktop-pet-webm';      Entry = 'packaging\pet_entry_no_chat.py'; NoChat = $true }
    'gif-chat'    = @{ Name = 'desktop-pet-gif-chat';  Entry = 'packaging\pet_entry.py'; Gif = $true }
    'gif'         = @{ Name = 'desktop-pet-gif';       Entry = 'packaging\pet_entry_no_chat.py'; Gif = $true; NoChat = $true }
}

if (-not $variants.ContainsKey($Variant)) {
    throw "Unknown variant: $Variant (available: $($variants.Keys -join ', '))"
}
$name  = $variants[$Variant].Name
$entry = $variants[$Variant].Entry
$isGif = $variants[$Variant].Gif
$noChat = $variants[$Variant].NoChat

$datas = if ($isGif) { 'assets/characters_gif;assets/characters_gif' } else { 'assets/characters;assets/characters' }
$excludes = if ($noChat) { @('--exclude-module', 'pet.chat', '--exclude-module', 'keyring') } else { @() }

$runtimeDlls = @()
# PyInstaller detects most VC runtime files automatically, but Qt6Core from
# current PySide6 builds also imports MSVCP140_1.dll.  Some conda layouts do
# not advertise that transitive dependency to PyInstaller, producing a bundle
# that builds successfully but fails at startup while importing QtCore.
foreach ($relativeDll in @(
    'Library\bin\sqlite3.dll',
    'Library\bin\ffi.dll',
    'Library\bin\liblzma.dll',
    'Library\bin\libbz2.dll',
    'Library\bin\libexpat.dll',
    'msvcp140_1.dll'
)) {
    $runtimeDll = Join-Path $pythonRoot $relativeDll
    if (Test-Path -LiteralPath $runtimeDll) {
        $runtimeDlls += @('--add-binary', "$runtimeDll;.")
    }
}

if ($isGif -and -not $Gif -and -not (Test-Path 'assets\characters_gif')) {
    $Gif = $true
}
if ($Gif -and -not $SkipBuild) {
    Write-Host "[1/3] Generating GIF assets..." -ForegroundColor Cyan
    & $py scripts\convert_to_gif.py --force --clean
    if ($LASTEXITCODE -ne 0) { throw "convert_to_gif failed: $LASTEXITCODE" }
}

if (-not $SkipBuild) {
    Write-Host "[0/3] Generating app icon..." -ForegroundColor Cyan
    & $py scripts\make_icon.py
    if ($LASTEXITCODE -ne 0) { throw "make_icon failed: $LASTEXITCODE" }

    # Clean previous build artifacts
    $appDir = Join-Path $root "dist-onedir\$name"
    $zip = Join-Path $root "dist-onedir\$name-portable.zip"
    if (Test-Path -LiteralPath $appDir) {
        Write-Host "Cleaning previous build output: $appDir ..." -ForegroundColor Yellow
        Remove-Item -Recurse -Force -LiteralPath $appDir -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $zip) {
        Remove-Item -Force -LiteralPath $zip -ErrorAction SilentlyContinue
    }

    Write-Host "[1/3] PyInstaller --onedir building $name ..." -ForegroundColor Cyan
    $variantPy = Join-Path $root 'packaging\build_variant.py'
    $variantVal = if ($Variant -eq 'desktop-pet') { '' } else { $Variant }
    [System.IO.File]::WriteAllText(
        $variantPy,
        "VARIANT = '$variantVal'`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    # python -m PyInstaller
    & $py -m PyInstaller --noconfirm --clean --onedir --windowed --noupx `
        --name $name `
        --distpath dist-onedir `
        --workpath build-onedir `
        --icon assets\icon.ico `
        --collect-all imageio_ffmpeg `
        --collect-all certifi `
        --add-data $datas `
        --add-data "assets\big_blue_fat_fish;assets\big_blue_fat_fish" `
        --add-data "pet\menu_templates;pet\menu_templates" `
        @runtimeDlls `
        @excludes `
        $entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }

    # PyInstaller searches every directory inherited through PATH.  In the
    # Codex build environment that can accidentally collect GNU/Poppler ICU
    # binaries named icuuc.dll/icudt*.dll.  They shadow Windows' system ICU
    # used by the official PySide6 wheel and make Qt6Core fail with WinError
    # 127 (procedure not found).  This application does not ship or need that
    # unrelated ICU pair.
    $internalDir = Join-Path $appDir '_internal'
    $foreignIcuUc = Join-Path $internalDir 'icuuc.dll'
    if (Test-Path -LiteralPath $foreignIcuUc) {
        Remove-Item -Force -LiteralPath $foreignIcuUc
    }
    Get-ChildItem -LiteralPath $internalDir -Filter 'icudt*.dll' -File -ErrorAction SilentlyContinue |
        Remove-Item -Force

    Remove-Item -Recurse -Force (Join-Path $root 'build-onedir') -ErrorAction SilentlyContinue
}

$appDir = Join-Path $root "dist-onedir\$name"
if (-not (Test-Path $appDir)) { throw "Build output missing: $appDir" }

if (-not $SkipCheck) {
    Write-Host "[1.5/3] Chinese-encoding self-check on bundle..." -ForegroundColor Cyan
    & $py scripts\check_bundle_encoding.py --dir $appDir
    if ($LASTEXITCODE -ne 0) {
        throw "Bundle encoding check failed - refusing to package garbled output"
    }
}

if (-not $SkipZip) {
    Write-Host "[2/3] Packing portable zip..." -ForegroundColor Cyan
    $zip = Join-Path $root "dist-onedir\$name-portable.zip"
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
    Compress-Archive -Path "$appDir\*" -DestinationPath $zip -CompressionLevel Optimal
    Write-Host "      $zip ($([math]::Round((Get-Item $zip).Length/1MB,1)) MB)" -ForegroundColor Green
}

Write-Host "[3/3] Done. onedir dir: $appDir" -ForegroundColor Green
Write-Host "      Executable: $appDir\$name.exe" -ForegroundColor Green
