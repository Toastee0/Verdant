# run.ps1 — build and launch verdant_f1.exe
# Always runs relative to the script's own directory (project root)
Set-Location $PSScriptRoot

$BinName = "verdant_f1"
$BinFile = "verdant_f1.exe"
$Dll     = "deps\raylib\lib\raylib.dll"

# ── 1. Check / kill existing instance ──────────────────────────────────────
$running = Get-Process -Name $BinName -ErrorAction SilentlyContinue
if ($running) {
    Write-Host "Killing $BinName (PID $($running.Id))..." -ForegroundColor Yellow
    Stop-Process -Id $running.Id -Force
    Start-Sleep -Milliseconds 400
} else {
    Write-Host "$BinName not running" -ForegroundColor DarkGray
}

# ── 2. Build ────────────────────────────────────────────────────────────────
Write-Host "Building $BinFile..." -ForegroundColor Cyan
$Srcs = @(
    "src/main.c", "src/noise.c", "src/world.c", "src/terrain.c", "src/input.c",
    "src/sim/dirt.c", "src/sim/water.c", "src/sim/impact.c", "src/sim/blob.c",
    "src/player.c", "src/rover.c", "src/rover_arm.c", "src/render.c"
)
$err = & gcc -O2 -Wall -Isrc -I deps/raylib/include @Srcs -o $BinFile `
    -L deps/raylib/lib `
    -lraylibdll -lopengl32 -lgdi32 -lwinmm 2>&1

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