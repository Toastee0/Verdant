# run.ps1 — build and launch verdant_f1.exe
# Always runs relative to the script's own directory (project root)
Set-Location $PSScriptRoot

$BinName = "verdant_f1"
$BinFile = "verdant_f1.exe"
$Dll     = "deps\raylib\lib\raylib.dll"

# ── 1. Check / kill existing instance ──────────────────────────────────────
$running = $null
if (Get-Command pslist -ErrorAction SilentlyContinue) {
    $psout = pslist $BinName 2>&1 | Out-String
    if ($psout -match $BinName) { $running = Get-Process -Name $BinName -ErrorAction SilentlyContinue }
} else {
    $running = Get-Process -Name $BinName -ErrorAction SilentlyContinue
}

if ($running) {
    Write-Host "Killing $BinName (PID $($running.Id))..." -ForegroundColor Yellow
    Stop-Process -Id $running.Id -Force
    Start-Sleep -Milliseconds 400
} else {
    Write-Host "$BinName not running" -ForegroundColor DarkGray
}

# ── 2. Build ────────────────────────────────────────────────────────────────
Write-Host "Building $BinFile..." -ForegroundColor Cyan
$err = & gcc verdant_f1.c -o $BinFile `
    -I deps/raylib/include `
    -L deps/raylib/lib `
    -lraylib -lopengl32 -lgdi32 -lwinmm 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD FAILED" -ForegroundColor Red
    Write-Host ($err | Out-String)
    exit 1
}
Write-Host "Build OK" -ForegroundColor Green

# ── 3. Ensure DLL is alongside the binary ───────────────────────────────────
if (-not (Test-Path "raylib.dll")) {
    Copy-Item $Dll "raylib.dll"
    Write-Host "Copied raylib.dll to project root"
}

# ── 4. Launch (detached, working dir = project root for asset paths) ─────────
Write-Host "Launching $BinFile..." -ForegroundColor Green
Start-Process -FilePath ".\$BinFile" -WorkingDirectory $PSScriptRoot
