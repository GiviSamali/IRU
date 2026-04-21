# build_windows.ps1 — сборка агента ИРУ (--onedir + ZIP) и загрузка на сервер.
#
# Запуск из корня репозитория IRU на Windows (PowerShell):
#
#   .\deploy\build_windows.ps1 -Version 3.7
#
# Параметры:
#   -Version      Строка версии (например "3.7"). ОБЯЗАТЕЛЬНО.
#   -Server       URL сервера (по умолчанию https://irumode.ru).
#   -Token        Admin-токен. Если не передан, берётся из env:IRU_ADMIN_TOKEN.
#   -SkipUpload   Только собрать, не загружать на сервер.
#
# Требования:
#   - Python 3.11+ в PATH
#   - agent\agent.py и ui\IruIcon.ico в репозитории
#
# КОДИРОВКА: UTF-8 с BOM (обязательно для PowerShell 5.1 на русской Windows)

param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Server = "https://irumode.ru",

    [string]$Token = $env:IRU_ADMIN_TOKEN,

    [switch]$SkipUpload
)

$ErrorActionPreference = "Stop"

# -- Пути ------------------------------------------------------------------
$repoRoot  = (Get-Item -Path "$PSScriptRoot\..").FullName
$agentDir  = Join-Path $repoRoot "agent"
$iconPath  = Join-Path $repoRoot "ui\IruIcon.ico"
$distDir   = Join-Path $repoRoot "dist"
$buildDir  = Join-Path $repoRoot "build"
$specPath  = Join-Path $repoRoot "agent.spec"

if (-not (Test-Path "$agentDir\agent.py")) {
    throw "Не найден agent\agent.py. Запускайте скрипт из репозитория IRU."
}
if (-not (Test-Path $iconPath)) {
    Write-Warning "Иконка $iconPath не найдена — собираем без иконки."
    $iconPath = $null
}

Write-Host "== Сборка agent v$Version (onedir + ZIP) ==" -ForegroundColor Cyan
Write-Host "Репозиторий: $repoRoot"

# -- Python + зависимости --------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python не найден в PATH." }

Write-Host "Python: $($py.Source)"
& python -m pip install --upgrade pip | Out-Null
& python -m pip install --upgrade pyinstaller websockets httpx | Out-Null

# -- Очистка ----------------------------------------------------------------
if (Test-Path $distDir)  { Remove-Item -Recurse -Force $distDir }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
if (Test-Path $specPath) { Remove-Item -Force $specPath }

# -- Сборка (--onedir) -----------------------------------------------------
$pyiArgs = @(
    "--onedir",
    "--name", "agent",
    "--noconsole",
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

# Точка входа
$pyiArgs += (Join-Path $agentDir "agent.py")

Push-Location $agentDir
try {
    Write-Host "Запуск PyInstaller (--onedir)..."
    & python -m PyInstaller @pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$exePath = Join-Path $distDir "agent\agent.exe"
if (-not (Test-Path $exePath)) {
    throw "После сборки не найден $exePath"
}

# -- VERSION.txt внутри папки agent -----------------------------------------
$versionTxt = Join-Path $distDir "agent\VERSION.txt"
Set-Content -Path $versionTxt -Value $Version -Encoding UTF8 -NoNewline

# -- Упаковка в ZIP (файлы на верхнем уровне!) ------------------------------
$zipPath = Join-Path $distDir "agent-v$Version.zip"
Compress-Archive -Path "$distDir\agent\*" -DestinationPath $zipPath -Force

$zipSize = (Get-Item $zipPath).Length
Write-Host ("Готово: {0} ({1:N0} байт)" -f $zipPath, $zipSize) -ForegroundColor Green

# -- Загрузка на сервер ----------------------------------------------------
if ($SkipUpload) {
    Write-Host "SkipUpload=true — загрузка пропущена."
    Write-Host "ZIP: $zipPath"
    exit 0
}

if (-not $Token) {
    throw "Не задан admin-токен. Передайте -Token или установите env:IRU_ADMIN_TOKEN."
}

$uri = "$Server/api/agent/upload?version=$Version"
Write-Host "Загрузка в $uri ..."

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($curl) {
    & curl.exe -sS -X POST $uri `
        -H "X-Token: $Token" `
        -H "Content-Type: application/octet-stream" `
        --data-binary "@$zipPath" `
        --fail-with-body
    if ($LASTEXITCODE -ne 0) { throw "curl вернул код $LASTEXITCODE" }
} else {
    $bytes = [System.IO.File]::ReadAllBytes($zipPath)
    $resp = Invoke-WebRequest -Uri $uri -Method Post `
        -Headers @{ "X-Token" = $Token; "Content-Type" = "application/octet-stream" } `
        -Body $bytes -UseBasicParsing
    Write-Host $resp.Content
}

Write-Host ""
Write-Host "OK: agent v$Version (ZIP) загружен. Агенты подтянут обновление автоматически." -ForegroundColor Green
