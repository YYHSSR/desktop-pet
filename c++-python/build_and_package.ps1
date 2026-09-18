# Hybrid edition packaging: C++ host + Qt runtime + assets + Python Worker.
# The output is fully self-contained (ffmpeg & character assets included);
# end users only need Python 3.10+ on PATH for the Worker process.
$ErrorActionPreference = "Stop"

$hybridDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # <repo>/c++-python
$cppDir = Join-Path $hybridDir "host"                          # C++ host project

# --- [1/4] Build the C++ host (same toolchain as the C++ edition) ---
Write-Host "=== [1/4] Building C++ host ===" -ForegroundColor Cyan
& "D:\python\miniconda\envs\py12\Lib\site-packages\cmake\data\bin\cmake.exe" --build (Join-Path $cppDir "build") --config Release
if ($LASTEXITCODE -ne 0) { throw "C++ build failed!" }

# --- [2/4] Assemble the distribution directory ---
Write-Host "=== [2/4] Assembling distribution ===" -ForegroundColor Cyan
$distDir = Join-Path $hybridDir "dist\desktop-pet-hybrid"
if (Test-Path -LiteralPath $distDir) {
    Remove-Item -Recurse -Force -LiteralPath $distDir -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Path $distDir -Force | Out-Null

$builtExe = Join-Path $cppDir "build\bin\desktop-pet.exe"
Copy-Item -Path $builtExe -Destination $distDir -Force
& "E:\msys\msys\mingw64\bin\windeployqt.exe" --no-translations --qmldir (Join-Path $cppDir "qml") (Join-Path $distDir "desktop-pet.exe")

# Headless platform plugins for smoke tests (same as the C++ edition)
$qtPlatformSource = "E:\msys\msys\mingw64\share\qt6\plugins\platforms"
foreach ($pluginName in @("qoffscreen.dll", "qminimal.dll")) {
    $pluginSource = Join-Path $qtPlatformSource $pluginName
    if (Test-Path -LiteralPath $pluginSource) {
        Copy-Item -LiteralPath $pluginSource -Destination (Join-Path $distDir "platforms") -Force
    }
}

# MinGW runtime DLLs (recursive dependency walk, same as the C++ edition)
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
            if ((Test-Path $candidate) -and -not (Test-Path (Join-Path $distDir $dll))) {
                Copy-Item $candidate (Join-Path $distDir $dll) -Force
                $queue.Enqueue((Join-Path $distDir $dll))
            }
        }
    }
}

# Assets (character WebM + ffmpeg) -- the hybrid edition ships its own copy
$distAssets = Join-Path $distDir "assets"
New-Item -ItemType Directory -Path $distAssets -Force | Out-Null
Copy-Item -Path (Join-Path $cppDir "assets\*") -Destination $distAssets -Recurse -Force

# qt.conf for portable QML plugin loading
$qtConfContent = @"
[Paths]
Prefix = .
Plugins = .
Imports = qml
QmlImports = qml
Qml2Imports = qml
"@
Set-Content -Path (Join-Path $distDir "qt.conf") -Value $qtConfContent -Encoding Ascii

# --- [3/4] Bundle the Python Worker (source only, no caches / tests / docs) ---
Write-Host "=== [3/4] Bundling Python Worker ===" -ForegroundColor Cyan
$workerDist = Join-Path $distDir "c++-python\worker\src"
New-Item -ItemType Directory -Path $workerDist -Force | Out-Null
$workerSrc = Join-Path $hybridDir "worker\src\pet_worker"
Copy-Item -Path $workerSrc -Destination $workerDist -Recurse -Force
Get-ChildItem -Path (Join-Path $workerDist "pet_worker") -Recurse -Directory |
    Where-Object { $_.Name -in @("__pycache__", ".pytest_cache") } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Usage note is the ONLY document shipped in the package
Copy-Item -Path (Join-Path $hybridDir "packaging\HYBRID_README.md") -Destination (Join-Path $distDir "README.md") -Force

# --- [4/4] Portable zip ---
Write-Host "=== [4/4] Creating Portable Zip Archive ===" -ForegroundColor Cyan
$zipFile = Join-Path $hybridDir "dist\desktop-pet-hybrid-portable.zip"
if (Test-Path -LiteralPath $zipFile) {
    Remove-Item -Force -LiteralPath $zipFile -ErrorAction SilentlyContinue
}
Compress-Archive -Path "$distDir\*" -DestinationPath $zipFile -CompressionLevel Optimal

Write-Host "=== HYBRID BUILD & PACKAGING COMPLETE! ===" -ForegroundColor Green
Write-Host "App Directory: $distDir" -ForegroundColor Green
Write-Host "Portable Zip: $zipFile" -ForegroundColor Green
