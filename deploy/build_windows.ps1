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
#   -DebugBuild   Собрать с --console (видимый stdout/stderr для отладки).
#                 ZIP будет называться IruAgent-debug.zip.
#
# Требования:
#   - Python 3.11+ в PATH
#   - agent\agent.py и иконка agent\IruIcon.ico (или fallback ui\IruIcon.ico)
#
# КОДИРОВКА: UTF-8 с BOM (обязательно для PowerShell 5.1 на русской Windows)

param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Server = "https://irumode.ru",

    [string]$Token = $env:IRU_ADMIN_TOKEN,

    [switch]$SkipUpload,

    [switch]$DebugBuild
)

$ErrorActionPreference = "Stop"

# -- Пути ------------------------------------------------------------------
$repoRoot  = (Get-Item -Path "$PSScriptRoot\..").FullName
$agentDir  = Join-Path $repoRoot "agent"
$iconPath  = Join-Path $agentDir "IruIcon.ico"
$fallbackIconPath = Join-Path $repoRoot "ui\IruIcon.ico"
$distDir   = Join-Path $repoRoot "dist"
$buildDir  = Join-Path $repoRoot "build"
$specPath  = Join-Path $repoRoot "IruAgent.spec"

if (-not (Test-Path "$agentDir\agent.py")) {
    throw "Не найден agent\agent.py. Запускайте скрипт из репозитория IRU."
}
if (-not (Test-Path $iconPath)) {
    if (Test-Path $fallbackIconPath) {
        $iconPath = $fallbackIconPath
    } else {
        Write-Warning "Иконка не найдена ни в agent, ни в ui — собираем без иконки."
        $iconPath = $null
    }
}

$modeLabel = if ($DebugBuild) { "DEBUG/console" } else { "windowed" }
Write-Host "== Сборка agent v$Version ($modeLabel, onedir + ZIP) ==" -ForegroundColor Cyan
Write-Host "Репозиторий: $repoRoot"

# -- Python + зависимости --------------------------------------------------
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python не найден в PATH." }

Write-Host "Python: $($py.Source)"
Write-Host "Обновляем pip..."
& python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade завершился с кодом $LASTEXITCODE" }

Write-Host "Устанавливаем зависимости сборки (pyinstaller, websockets, httpx, PySide6)..."
& python -m pip install --upgrade pyinstaller websockets httpx PySide6
if ($LASTEXITCODE -ne 0) { throw "pip install завершился с кодом $LASTEXITCODE" }

Write-Host "Зависимости установлены." -ForegroundColor DarkGray

# -- Очистка ----------------------------------------------------------------
if (Test-Path $distDir)  { Remove-Item -Recurse -Force $distDir }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
if (Test-Path $specPath) { Remove-Item -Force $specPath }

# -- Сборка (--onedir) -----------------------------------------------------
$pyiArgs = @(
    "--onedir",
    "--name", "IruAgent",
    $(if ($DebugBuild) { "--console" } else { "--noconsole" }),
    "--distpath", $distDir,
    "--workpath", $buildDir,
    "--specpath", $repoRoot,
    "--noconfirm",
    "--collect-submodules", "core",
    "--collect-submodules", "ui",
    "--collect-submodules", "platforms",
    "--collect-all", "PySide6",
    "--hidden-import", "core",
    "--hidden-import", "ui",
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

$exePath = Join-Path $distDir "IruAgent\IruAgent.exe"
if (-not (Test-Path $exePath)) {
    throw "После сборки не найден $exePath"
}

# -- VERSION.txt внутри папки agent -----------------------------------------
# Set-Content -Encoding UTF8 в PowerShell 5.1 пишет BOM (EF BB BF),
# что ломает сравнение версий на агенте. WriteAllText пишет без BOM.
$versionTxt = Join-Path $distDir "IruAgent\VERSION.txt"
[System.IO.File]::WriteAllText($versionTxt, $Version, [System.Text.UTF8Encoding]::new($false))

# -- Упаковка в ZIP (папка IruAgent/ на верхнем уровне) ---------------------
$zipName = if ($DebugBuild) { "IruAgent-debug.zip" } else { "IruAgent.zip" }
$zipPath = Join-Path $distDir $zipName
Compress-Archive -Path "$distDir\IruAgent" -DestinationPath $zipPath -Force

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
