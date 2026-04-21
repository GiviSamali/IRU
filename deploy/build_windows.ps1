# build_windows.ps1 -- sborka agent.exe pod Windows i zagruzka na server.
#
# Zapusk iz kornya repozitoriya IRU na Windows (PowerShell):
#
#   .\deploy\build_windows.ps1 -Version 3.7
#
# Parametry:
#   -Version      Stroka versii (naprimer "3.7"). OBYAZATELNO.
#   -Server       URL servera (po umolchaniyu https://irumode.ru).
#   -Token        Admin-token. Esli ne peredan, beryotsya iz env:IRU_ADMIN_TOKEN.
#   -SkipUpload   Tolko sobrat, ne zagruzhat na server.
#
# Trebovaniya:
#   - Python 3.11+ v PATH
#   - pip install pyinstaller
#   - agent\agent.py i ui\IruIcon.ico v repozitorii

param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Server = "https://irumode.ru",

    [string]$Token = $env:IRU_ADMIN_TOKEN,

    [switch]$SkipUpload
)

$ErrorActionPreference = "Stop"

# -- Proverki -------------------------------------------------------------
$repoRoot  = (Get-Item -Path "$PSScriptRoot\..").FullName
$agentDir  = Join-Path $repoRoot "agent"
$iconPath  = Join-Path $repoRoot "ui\IruIcon.ico"
$distDir   = Join-Path $repoRoot "dist"
$buildDir  = Join-Path $repoRoot "build"
$specPath  = Join-Path $repoRoot "agent.spec"

if (-not (Test-Path "$agentDir\agent.py")) {
    throw "Ne nayden agent\agent.py. Zapuskay skript iz repozitoriya IRU."
}
if (-not (Test-Path $iconPath)) {
    Write-Warning "Ikonka $iconPath ne naydena -- sobirayu bez ikonki."
    $iconPath = $null
}

Write-Host "== Sborka agent.exe v$Version ==" -ForegroundColor Cyan
Write-Host "Repozitoriy: $repoRoot"

# -- Python + PyInstaller -------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python ne nayden v PATH." }

Write-Host "Python: $($py.Source)"
& python -m pip install --upgrade pip | Out-Null
& python -m pip install --upgrade pyinstaller websockets httpx | Out-Null

# -- Ochistka -------------------------------------------------------------
if (Test-Path $distDir)  { Remove-Item -Recurse -Force $distDir }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
if (Test-Path $specPath) { Remove-Item -Force $specPath }

# -- Sborka ---------------------------------------------------------------
$pyiArgs = @(
    "--onefile",
    "--name", "agent",
    "--distpath", $distDir,
    "--workpath", $buildDir,
    "--specpath", $repoRoot,
    "--noconfirm",
    "--collect-submodules", "platforms",
    "--hidden-import", "platforms",
    "--hidden-import", "platforms.windows",
    "--hidden-import", "platforms.linux"
)
if ($iconPath) { $pyiArgs += @("--icon", $iconPath) }

# Dlya Windows agent -- bez konsoli (fonovyi rezhim)
$pyiArgs += @("--noconsole")

# Tochka vhoda
$pyiArgs += (Join-Path $agentDir "agent.py")

Push-Location $agentDir
try {
    Write-Host "Zapusk PyInstaller..."
    & python -m PyInstaller @pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller zavershilsya s kodom $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$exePath = Join-Path $distDir "agent.exe"
if (-not (Test-Path $exePath)) {
    throw "Posle sborki ne nayden $exePath"
}

$size = (Get-Item $exePath).Length
Write-Host ("Gotovo: {0} ({1:N0} bayt)" -f $exePath, $size) -ForegroundColor Green

# -- Zagruzka na server ---------------------------------------------------
if ($SkipUpload) {
    Write-Host "SkipUpload=true -- zagruzka propushchena."
    exit 0
}

if (-not $Token) {
    throw "Ne zadan admin-token. Pereday -Token ili ustanovi env:IRU_ADMIN_TOKEN."
}

$uri = "$Server/api/agent/upload?version=$Version"
Write-Host "Zagruzka v $uri ..."

# curl.exe idyot v Windows 10/11 iz korobki; PowerShell Invoke-WebRequest tozhe podhodit,
# no curl nadyozhnee dlya bolshih binarnyh tel.
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($curl) {
    & curl.exe -sS -X POST $uri `
        -H "X-Token: $Token" `
        -H "Content-Type: application/octet-stream" `
        --data-binary "@$exePath" `
        --fail-with-body
    if ($LASTEXITCODE -ne 0) { throw "curl vernul kod $LASTEXITCODE" }
} else {
    $bytes = [System.IO.File]::ReadAllBytes($exePath)
    $resp = Invoke-WebRequest -Uri $uri -Method Post `
        -Headers @{ "X-Token" = $Token; "Content-Type" = "application/octet-stream" } `
        -Body $bytes -UseBasicParsing
    Write-Host $resp.Content
}

Write-Host ""
Write-Host "OK: agent.exe v$Version zagruzhen. Agenty podtyanut obnovlenie avtomaticheski." -ForegroundColor Green
