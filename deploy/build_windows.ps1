# build_windows.ps1 — сборка agent.exe под Windows и загрузка на сервер.
#
# Запуск из корня репозитория IRU на Windows (PowerShell):
#
#   .\deploy\build_windows.ps1 -Version 3.7
#
# Параметры:
#   -Version      Строка версии (например "3.7"). ОБЯЗАТЕЛЬНО.
#   -Server       URL сервера (по умолчанию https://irumode.ru).
#   -Token        Админ-токен. Если не передан, берётся из env:IRU_ADMIN_TOKEN.
#   -SkipUpload   Только собрать, не загружать на сервер.
#
# Требования:
#   - Python 3.11+ в PATH
#   - pip install pyinstaller
#   - agent\agent.py и ui\IruIcon.ico в репозитории

param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Server = "https://irumode.ru",

    [string]$Token = $env:IRU_ADMIN_TOKEN,

    [switch]$SkipUpload
)

$ErrorActionPreference = "Stop"

# ── Проверки ──────────────────────────────────────────────────────────────
$repoRoot  = (Get-Item -Path "$PSScriptRoot\..").FullName
$agentDir  = Join-Path $repoRoot "agent"
$iconPath  = Join-Path $repoRoot "ui\IruIcon.ico"
$distDir   = Join-Path $repoRoot "dist"
$buildDir  = Join-Path $repoRoot "build"
$specPath  = Join-Path $repoRoot "agent.spec"

if (-not (Test-Path "$agentDir\agent.py")) {
    throw "Не найден agent\agent.py. Запускай скрипт из репозитория IRU."
}
if (-not (Test-Path $iconPath)) {
    Write-Warning "Иконка $iconPath не найдена — собираю без иконки."
    $iconPath = $null
}

Write-Host "── Сборка agent.exe v$Version ──" -ForegroundColor Cyan
Write-Host "Репозиторий: $repoRoot"

# ── Python + PyInstaller ──────────────────────────────────────────────────
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python не найден в PATH." }

Write-Host "Python: $($py.Source)"
& python -m pip install --upgrade pip | Out-Null
& python -m pip install --upgrade pyinstaller websockets httpx | Out-Null

# ── Очистка ───────────────────────────────────────────────────────────────
if (Test-Path $distDir)  { Remove-Item -Recurse -Force $distDir }
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }
if (Test-Path $specPath) { Remove-Item -Force $specPath }

# ── Сборка ────────────────────────────────────────────────────────────────
$pyiArgs = @(
    "--onefile",
    "--name", "agent",
    "--distpath", $distDir,
    "--workpath", $buildDir,
    "--specpath", $repoRoot,
    "--noconfirm",
    # platforms — папка с windows.py/linux.py, подтянется как модуль
    "--collect-submodules", "platforms",
    "--hidden-import", "platforms",
    "--hidden-import", "platforms.windows",
    "--hidden-import", "platforms.linux"
)
if ($iconPath) { $pyiArgs += @("--icon", $iconPath) }

# Для Windows агент — без консоли (фоновый режим)
$pyiArgs += @("--noconsole")

# Точка входа
$pyiArgs += (Join-Path $agentDir "agent.py")

Push-Location $agentDir
try {
    Write-Host "Запуск PyInstaller…"
    & python -m PyInstaller @pyiArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$exePath = Join-Path $distDir "agent.exe"
if (-not (Test-Path $exePath)) {
    throw "После сборки не найден $exePath"
}

$size = (Get-Item $exePath).Length
Write-Host ("Готово: {0} ({1:N0} байт)" -f $exePath, $size) -ForegroundColor Green

# ── Загрузка на сервер ────────────────────────────────────────────────────
if ($SkipUpload) {
    Write-Host "SkipUpload=true — загрузка пропущена."
    exit 0
}

if (-not $Token) {
    throw "Не задан админ-токен. Передай -Token или установи env:IRU_ADMIN_TOKEN."
}

$uri = "$Server/api/agent/upload?version=$Version"
Write-Host "Загрузка в $uri …"

# curl.exe идёт в Windows 10/11 из коробки; PowerShell Invoke-WebRequest тоже подходит,
# но curl надёжнее для больших бинарных тел.
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if ($curl) {
    & curl.exe -sS -X POST $uri `
        -H "X-Token: $Token" `
        -H "Content-Type: application/octet-stream" `
        --data-binary "@$exePath" `
        --fail-with-body
    if ($LASTEXITCODE -ne 0) { throw "curl вернул код $LASTEXITCODE" }
} else {
    $bytes = [System.IO.File]::ReadAllBytes($exePath)
    $resp = Invoke-WebRequest -Uri $uri -Method Post `
        -Headers @{ "X-Token" = $Token; "Content-Type" = "application/octet-stream" } `
        -Body $bytes -UseBasicParsing
    Write-Host $resp.Content
}

Write-Host ""
Write-Host "✓ agent.exe v$Version загружен. Агенты подтянут обновление автоматически." -ForegroundColor Green
