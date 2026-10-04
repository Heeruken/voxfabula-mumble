# Build di Vox Fabula Companion in UN comando, dalla radice del progetto:
#     powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Produce in dist\:
#   Vox Fabula Companion\                 l'app (cartella)
#   VoxFabula-Companion-<ver>-portatile.zip   da scompattare e usare senza installare
#   VoxFabula-Companion-Setup-<ver>.exe       installer (se Inno Setup 6 e' installato)
#   + lo stesso installer coi nomi vecchi (VoxFabula-Voice-, NWN-Voce-), per l'aggiornamento automatico
# e stampa l'impronta SHA-256 di ogni file (da controllare su VirusTotal).
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$version = (Select-String -Path 'nwn_voce\__init__.py' -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "Vox Fabula Companion $version" -ForegroundColor Cyan

# 1) i test devono passare: niente build di una versione rotta
Write-Host "Test..." -ForegroundColor Cyan
python -m unittest -q tests.test_nwn_voce tests.test_updater tests.test_relay tests.test_cinema
if ($LASTEXITCODE -ne 0) { throw "Test falliti: build annullata." }

# 2) Mumble portatile + plugin fresco
if (-not (Test-Path 'vendor\mumble\mumble.exe')) {
    throw "Manca vendor\mumble\mumble.exe (Mumble portatile). Vedi README, sezione Build."
}
Copy-Item 'plugin\vc_range.dll' 'vendor\mumble\plugins\vc_range.dll' -Force

# 3) pulizia e build
foreach ($d in 'build', 'dist') { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
python -m PyInstaller packaging\nwn-voce.spec --noconfirm --distpath dist --workpath build\pyi --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fallito." }

# 4) zip portatile
$zip = "dist\VoxFabula-Companion-$version-portatile.zip"
Compress-Archive -Path 'dist\Vox Fabula Companion' -DestinationPath $zip -CompressionLevel Optimal

# 5) installer
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc /Q "/DMyAppVersion=$version" 'packaging\installer.iss'
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup fallito." }
    # Lo STESSO installer anche coi nomi di prima: le app gia' installate cercano
    # nella Release solo il loro nome (1.2.1-1.2.x: VoxFabula-Voice-Setup-*.exe,
    # 1.2.0: NWN-Voce-Setup-*.exe). Vanno caricati finche' qualcuno puo' averle.
    Copy-Item "dist\VoxFabula-Companion-Setup-$version.exe" "dist\VoxFabula-Voice-Setup-$version.exe"
    Copy-Item "dist\VoxFabula-Companion-Setup-$version.exe" "dist\NWN-Voce-Setup-$version.exe"
} else {
    Write-Warning "Inno Setup 6 non trovato: niente installer, solo lo zip."
}

# 6) riepilogo con impronte
Write-Host ""
Get-ChildItem dist -File | ForEach-Object {
    $h = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    '{0,8:N1} MB  {1}' -f ($_.Length / 1MB), $_.Name | Write-Host -ForegroundColor Green
    "            SHA-256 $h" | Write-Host
}
