# PowerShell script to compile and package Desktop Pet C++20 + Qt 6
$ErrorActionPreference = 'Stop'

$originalScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# 项目路径已全英文，无需虚拟盘映射（moc.exe 的 ANSI bug 仅在非 ASCII 路径下触发）
Set-Location $originalScriptDir
$workDir = $originalScriptDir

# Add MinGW and CMake/Ninja to PATH
$env:PATH = "E:\msys\msys\mingw64\bin;D:\python\miniconda\envs\py12\Scripts;$env:PATH"

Write-Host "=== [1/4] Configuring CMake Project ===" -ForegroundColor Cyan
& "D:\python\miniconda\envs\py12\Scripts\cmake.exe" `
    -G "Ninja" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_C_COMPILER="E:\msys\msys\mingw64\bin\gcc.exe" `
    -DCMAKE_CXX_COMPILER="E:\msys\msys\mingw64\bin\g++.exe" `
    -DCMAKE_MAKE_PROGRAM="D:\python\miniconda\envs\py12\Scripts\ninja.exe" `
    -DCMAKE_PREFIX_PATH="E:\msys\msys\mingw64" `
    -B "$workDir\build"

if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed!" }

Write-Host "=== [2/4] Compiling Project (C++20 Release) ===" -ForegroundColor Cyan
& "D:\python\miniconda\envs\py12\Scripts\cmake.exe" --build "$workDir\build" --config Release

if ($LASTEXITCODE -ne 0) { throw "Compilation failed!" }

Write-Host "=== [3/4] Packaging Application with windeployqt ===" -ForegroundColor Cyan
$distDir = Join-Path $workDir "dist\desktop-pet"
if (Test-Path -LiteralPath $distDir) {
    Remove-Item -Recurse -Force -LiteralPath $distDir -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $distDir -Force | Out-Null

$builtExe = Join-Path $workDir "build\bin\desktop-pet.exe"
if (-not (Test-Path -LiteralPath $builtExe)) {
    $builtExe = (Get-ChildItem -Path "$workDir\build" -Filter "desktop-pet.exe" -Recurse | Select-Object -First 1).FullName
}
Copy-Item -Path $builtExe -Destination $distDir -Force

# Deploy Qt6 dependencies using windeployqt
# --no-translations：界面文案全部硬编码中文，不使用 Qt 翻译，避免 catalogs.json 缺失警告
& "E:\msys\msys\mingw64\bin\windeployqt.exe" --no-translations --qmldir (Join-Path $workDir "qml") (Join-Path $distDir "desktop-pet.exe")

# Keep headless/minimal platform plugins in the portable package so CI and
# automated smoke tests exercise the packaged binary without showing a window.
$qtPlatformSource = "E:\msys\msys\mingw64\share\qt6\plugins\platforms"
$distPlatforms = Join-Path $distDir "platforms"
foreach ($pluginName in @("qoffscreen.dll", "qminimal.dll")) {
    $pluginSource = Join-Path $qtPlatformSource $pluginName
    if (Test-Path -LiteralPath $pluginSource) {
        Copy-Item -LiteralPath $pluginSource -Destination $distPlatforms -Force
    }
}

# Deploy MinGW C/C++ runtime and 3rd-party dependencies
Write-Host "Resolving all recursive DLL dependencies from MinGW..." -ForegroundColor Cyan
$binDir = "E:\msys\msys\mingw64\bin"
$found = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$queue = [System.Collections.Generic.Queue[string]]::new()
Get-ChildItem -Path $distDir -Filter "*.dll" -Recurse | ForEach-Object { $queue.Enqueue($_.FullName) }
Get-ChildItem -Path $distDir -Filter "*.exe" -Recurse | ForEach-Object { $queue.Enqueue($_.FullName) }

while ($queue.Count -gt 0) {
    $currentFile = $queue.Dequeue()
    $dump = & "$binDir\objdump.exe" -p $currentFile | Select-String "DLL Name:\s*(.+)"
    foreach ($match in $dump) {
        $dll = $match.Matches[0].Groups[1].Value.Trim()
        if (-not $found.Contains($dll)) {
            $found.Add($dll) | Out-Null
            $candidate = Join-Path $binDir $dll
            if (Test-Path $candidate) {
                $targetDll = Join-Path $distDir $dll
                if (-not (Test-Path $targetDll)) {
                    Copy-Item $candidate $targetDll -Force
                    $queue.Enqueue($targetDll)
                }
            }
        }
    }
}

# Copy assets into the release directory.
$assetsSource = Join-Path $workDir "assets"
$distAssets = Join-Path $distDir "assets"
if (Test-Path $assetsSource) {
    New-Item -ItemType Directory -Path $distAssets -Force | Out-Null
    Copy-Item -Path (Join-Path $assetsSource '*') -Destination $distAssets -Recurse -Force
}

# Copy ffmpeg.exe
$ffmpegSource = Join-Path $workDir "ffmpeg.exe"
if (Test-Path $ffmpegSource) {
    Copy-Item -Path $ffmpegSource -Destination $distDir -Force
}

# Ensure qt.conf is present for portable QML plugin loading
$qtConfContent = @"
[Paths]
Prefix = .
Plugins = .
Imports = qml
QmlImports = qml
Qml2Imports = qml
"@
Set-Content -Path (Join-Path $distDir "qt.conf") -Value $qtConfContent -Encoding Ascii

# Create root one-click launcher
$batContent = "@echo off`r`ncd /d `"%~dp0dist\desktop-pet`"`r`nstart `"`" `"%~dp0dist\desktop-pet\desktop-pet.exe`""
Set-Content -Path (Join-Path $originalScriptDir "run.bat") -Value $batContent -Encoding Ascii

Write-Host "=== [4/4] Creating Portable Zip Archive ===" -ForegroundColor Cyan
$zipFile = Join-Path $workDir "dist\desktop-pet-cpp-portable.zip"
if (Test-Path -LiteralPath $zipFile) {
    Remove-Item -Force -LiteralPath $zipFile -ErrorAction SilentlyContinue
}
Compress-Archive -Path "$distDir\*" -DestinationPath $zipFile -CompressionLevel Optimal

Write-Host "=== BUILD & PACKAGING COMPLETE! ===" -ForegroundColor Green
Write-Host "App Directory: $(Join-Path $originalScriptDir 'dist\desktop-pet')" -ForegroundColor Green
Write-Host "Portable Zip: $(Join-Path $originalScriptDir 'dist\desktop-pet-cpp-portable.zip')" -ForegroundColor Green
