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
#   -BuildShell   Дополнительно собрать локальный Agent Shell wrapper (IruShell).
#   -ShellWebUrl  URL Web UI для shell_config.json. По умолчанию равен -Server, только при -BuildShell.
#   -SkipShellZip Не упаковывать IruShell.zip.
#
# Требования:
#   - Python 3.11+ в PATH
#   - agent\agent.py
#   - для красивой иконки: agent\IruIcon.ico (или fallback ui\IruIcon.ico)
#
# КОДИРОВКА: UTF-8 с BOM (обязательно для PowerShell 5.1 на русской Windows)

param(
    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$Server = "https://irumode.ru",

    [string]$Token = $env:IRU_ADMIN_TOKEN,

    [switch]$SkipUpload,

    [switch]$DebugBuild,

    [switch]$BuildShell,

    [string]$ShellWebUrl = "",

    [switch]$SkipShellZip
)

$ErrorActionPreference = "Stop"

function Get-RunningDistAgentProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DistRoot
    )

    $distPrefix = ([System.IO.Path]::GetFullPath($DistRoot)).TrimEnd('\') + '\'
    return Get-Process -Name "IruAgent" -ErrorAction SilentlyContinue | Where-Object {
        try {
            $procPath = $_.Path
            if (-not $procPath) { return $false }
            $fullProcPath = [System.IO.Path]::GetFullPath($procPath)
            return $fullProcPath.StartsWith($distPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        } catch {
            return $false
        }
    }
}

function Remove-BuildArtifactsWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DistDir,
        [Parameter(Mandatory = $true)]
        [string]$BuildDir,
        [Parameter(Mandatory = $true)]
        [string]$SpecPath
    )

    $cleanupTargets = @(
        @{ Path = $DistDir; Kind = "directory" },
        @{ Path = $BuildDir; Kind = "directory" },
        @{ Path = $SpecPath; Kind = "file" }
    )

    $attempts = 6
    for ($attempt = 1; $attempt -le $attempts; $attempt++) {
        $lastError = $null
        $allRemoved = $true

        foreach ($target in $cleanupTargets) {
            $path = $target.Path
            if (-not (Test-Path $path)) {
                continue
            }

            try {
                if ($target.Kind -eq "directory") {
                    Remove-Item -Recurse -Force $path
                } else {
                    Remove-Item -Force $path
                }
            } catch {
                $allRemoved = $false
                $lastError = $_
                break
            }
        }

        if ($allRemoved) {
            return
        }

        if ($attempt -lt $attempts) {
            Write-Warning ("Очистка build/dist не удалась (попытка {0}/{1}). Ждём 2 сек и пробуем ещё раз..." -f $attempt, $attempts)
            Start-Sleep -Seconds 2
        } else {
            throw @"
Не удалось очистить старые артефакты сборки даже после нескольких попыток.
В этот момент файлы всё ещё были заняты внешним процессом.
Чаще всего это:
- ещё не завершившийся IruAgent.exe из dist
- OneDrive/Explorer/антивирус, которые держат новые dll сразу после запуска
Исходная ошибка: $($lastError.Exception.Message)
"@
        }
    }
}

function Publish-AgentBuild {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DistRoot,
        [Parameter(Mandatory = $true)]
        [string]$Version
    )

    if (-not (Test-Path $DistRoot)) {
        New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
    }

    $preferredTarget = Join-Path $DistRoot "IruAgent"
    $targetDir = $preferredTarget

    if (Test-Path $preferredTarget) {
        try {
            Remove-Item -Recurse -Force $preferredTarget -ErrorAction Stop
        } catch {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $targetDir = Join-Path $DistRoot ("IruAgent-v{0}-{1}" -f $Version, $stamp)
            Write-Warning ("Не удалось заменить dist\\IruAgent; публикуем новую папку в {0}" -f $targetDir)
        }
    }

    if (Test-Path $targetDir) {
        Remove-Item -Recurse -Force $targetDir -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

    Get-ChildItem -LiteralPath $SourceDir -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetDir -Recurse -Force
    }

    return $targetDir
}

function Publish-BuildArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DistRoot,
        [Parameter(Mandatory = $true)]
        [string]$ArtifactName,
        [Parameter(Mandatory = $true)]
        [string]$Version
    )

    if (-not (Test-Path $DistRoot)) {
        New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null
    }

    $preferredTarget = Join-Path $DistRoot $ArtifactName
    $targetDir = $preferredTarget

    if (Test-Path $preferredTarget) {
        try {
            Remove-Item -Recurse -Force $preferredTarget -ErrorAction Stop
        } catch {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $targetDir = Join-Path $DistRoot ("{0}-v{1}-{2}" -f $ArtifactName, $Version, $stamp)
            Write-Warning ("Не удалось заменить dist\\{0}; публикуем новую папку в {1}" -f $ArtifactName, $targetDir)
        }
    }

    if (Test-Path $targetDir) {
        Remove-Item -Recurse -Force $targetDir -ErrorAction SilentlyContinue
    }
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null

    Get-ChildItem -LiteralPath $SourceDir -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetDir -Recurse -Force
    }

    return $targetDir
}

function Test-PythonModule {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ModuleName
    )

    & python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$ModuleName') else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Get-GitValue {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [string]$Fallback = "unknown"
    )

    try {
        $value = (& git @Arguments 2>$null | Select-Object -First 1)
        if ($value) { return [string]$value }
    } catch {}
    return $Fallback
}

function Write-BuildInfo {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TargetDir,
        [Parameter(Mandatory = $true)]
        [string]$Version,
        [Parameter(Mandatory = $true)]
        [string]$Artifact,
        [string]$WebUrl = ""
    )

    $info = [ordered]@{
        version = $Version
        git_commit = Get-GitValue -Arguments @("rev-parse", "HEAD")
        branch = Get-GitValue -Arguments @("branch", "--show-current")
        built_at = (Get-Date).ToUniversalTime().ToString("o")
        artifact = $Artifact
    }
    if ($WebUrl) {
        $info.web_url = $WebUrl
    }

    $json = $info | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText((Join-Path $TargetDir "BUILD_INFO.json"), $json, [System.Text.UTF8Encoding]::new($false))
}

# -- Пути ------------------------------------------------------------------
$repoRoot  = (Get-Item -Path "$PSScriptRoot\..").FullName
$agentDir  = Join-Path $repoRoot "agent"
$iconPath  = Join-Path $agentDir "IruIcon.ico"
$fallbackIconPath = Join-Path $repoRoot "ui\IruIcon.ico"
$distDir   = Join-Path $repoRoot "dist"
$buildDir  = Join-Path $repoRoot "build"
$specPath  = Join-Path $repoRoot "IruAgent.spec"
$shellSpecPath = Join-Path $repoRoot "IruShell.spec"
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("iru-agent-build-" + [guid]::NewGuid().ToString("N"))
$stagingDistDir = Join-Path $stagingRoot "dist"
$stagingBuildDir = Join-Path $stagingRoot "build"
$stagingSpecDir = Join-Path $stagingRoot "spec"

if (-not (Test-Path "$agentDir\agent.py")) {
    throw "Не найден agent\agent.py. Запускайте скрипт из репозитория IRU."
}
if ($BuildShell -and -not (Test-Path "$agentDir\shell\main.py")) {
    throw "Не найден agent\shell\main.py. Agent Shell WebView v1 должен быть в репозитории."
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
if ($BuildShell) {
    if (-not $ShellWebUrl) {
        $ShellWebUrl = $Server
    }
    Write-Host "Agent Shell Web URL: $ShellWebUrl" -ForegroundColor DarkGray
}

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

# -- Проверка занятого dist --------------------------------------------------
$runningDistAgents = Get-RunningDistAgentProcesses -DistRoot $distDir
if ($runningDistAgents) {
    $processList = ($runningDistAgents | ForEach-Object {
        "{0} (PID {1})" -f $_.ProcessName, $_.Id
    }) -join ", "
    Write-Warning @"
Найдена запущенная сборка агента из папки dist: $processList
Соберём новую версию во временную staging-папку.
Если dist\IruAgent останется занятым, готовая папка будет опубликована под новым именем, а ZIP всё равно соберётся.
"@
}

# -- Подготовка staging ------------------------------------------------------
New-Item -ItemType Directory -Path $stagingDistDir -Force | Out-Null
New-Item -ItemType Directory -Path $stagingBuildDir -Force | Out-Null
New-Item -ItemType Directory -Path $stagingSpecDir -Force | Out-Null

# -- Сборка (--onedir) -----------------------------------------------------
$qtHiddenImports = @(
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets"
)

$qtExcludedModules = @(
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickDialogs2",
    "PySide6.QtQuickDialogs2QuickImpl",
    "PySide6.QtQuickTemplates2",
    "PySide6.QtQuickTest",
    "PySide6.QtQuickWidgets",
    "PySide6.QtShaderTools",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets"
)

$pyiArgs = @(
    "--onedir",
    "--name", "IruAgent",
    $(if ($DebugBuild) { "--console" } else { "--noconsole" }),
    "--distpath", $stagingDistDir,
    "--workpath", $stagingBuildDir,
    "--specpath", $stagingSpecDir,
    "--noconfirm",
    "--collect-submodules", "core",
    "--collect-submodules", "ui",
    "--collect-submodules", "platforms",
    "--hidden-import", "core",
    "--hidden-import", "ui",
    "--hidden-import", "platforms",
    "--hidden-import", "platforms.windows",
    "--hidden-import", "platforms.linux"
)

foreach ($module in $qtHiddenImports) {
    $pyiArgs += @("--hidden-import", $module)
}
foreach ($module in $qtExcludedModules) {
    $pyiArgs += @("--exclude-module", $module)
}

if ($iconPath) {
    $pyiArgs += @("--icon", $iconPath)
}

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

$exePath = Join-Path $stagingDistDir "IruAgent\IruAgent.exe"
if (-not (Test-Path $exePath)) {
    throw "После сборки не найден $exePath"
}

# -- Явно положить icon рядом с exe для tray/UI -----------------------------
if ($iconPath) {
    $distIconPath = Join-Path $stagingDistDir "IruAgent\IruIcon.ico"
    Copy-Item -LiteralPath $iconPath -Destination $distIconPath -Force
}

# -- VERSION.txt внутри папки agent -----------------------------------------
# Set-Content -Encoding UTF8 в PowerShell 5.1 пишет BOM (EF BB BF),
# что ломает сравнение версий на агенте. WriteAllText пишет без BOM.
$versionTxt = Join-Path $stagingDistDir "IruAgent\VERSION.txt"
[System.IO.File]::WriteAllText($versionTxt, $Version, [System.Text.UTF8Encoding]::new($false))
Write-BuildInfo -TargetDir (Join-Path $stagingDistDir "IruAgent") -Version $Version -Artifact "IruAgent"

# -- Публикация папки сборки в repo dist ------------------------------------
$publishedAgentDir = Publish-AgentBuild -SourceDir (Join-Path $stagingDistDir "IruAgent") -DistRoot $distDir -Version $Version

# -- Упаковка в ZIP (папка IruAgent/ на верхнем уровне) ---------------------
$zipName = if ($DebugBuild) { "IruAgent-debug.zip" } else { "IruAgent.zip" }
$zipPath = Join-Path $distDir $zipName
if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-Archive -Path (Join-Path $stagingDistDir "IruAgent") -DestinationPath $zipPath -Force

$zipSize = (Get-Item $zipPath).Length
Write-Host ("Готово: {0} ({1:N0} байт)" -f $zipPath, $zipSize) -ForegroundColor Green
Write-Host ("Папка сборки: {0}" -f $publishedAgentDir) -ForegroundColor DarkGray
try {
    $dirBytes = (Get-ChildItem $publishedAgentDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
    Write-Host ("Размер папки сборки: {0:N0} байт" -f $dirBytes) -ForegroundColor DarkGray
} catch {
    Write-Warning "Не удалось посчитать размер папки сборки."
}

# -- Optional Agent Shell build --------------------------------------------
if ($BuildShell) {
    Write-Host ""
    Write-Host "== Сборка Agent Shell v$Version (IruShell, локальный artifact) ==" -ForegroundColor Cyan

    $shellHiddenImports = @(
        "agent",
        "agent.shell",
        "agent.shell.main",
        "agent.shell.config",
        "agent.shell.status",
        "agent.shell.tray"
    )

    if (Test-PythonModule -ModuleName "webview") {
        $shellHiddenImports += "webview"
    } else {
        Write-Host "pywebview не установлен — IruShell будет использовать browser fallback." -ForegroundColor DarkGray
    }
    if (Test-PythonModule -ModuleName "pystray") {
        $shellHiddenImports += "pystray"
    } else {
        Write-Host "pystray не установлен — tray будет отключен." -ForegroundColor DarkGray
    }
    if (Test-PythonModule -ModuleName "PIL") {
        $shellHiddenImports += @("PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont")
    } else {
        Write-Host "Pillow не установлен — tray icon будет недоступен." -ForegroundColor DarkGray
    }

    $shellPyiArgs = @(
        "--onedir",
        "--name", "IruShell",
        $(if ($DebugBuild) { "--console" } else { "--noconsole" }),
        "--distpath", $stagingDistDir,
        "--workpath", $stagingBuildDir,
        "--specpath", $stagingSpecDir,
        "--noconfirm",
        "--paths", $repoRoot,
        "--collect-submodules", "agent.shell"
    )

    foreach ($module in $shellHiddenImports) {
        $shellPyiArgs += @("--hidden-import", $module)
    }
    if ($iconPath) {
        $shellPyiArgs += @("--icon", $iconPath)
    }

    $shellPyiArgs += (Join-Path $agentDir "shell\main.py")

    Push-Location $repoRoot
    try {
        Write-Host "Запуск PyInstaller для IruShell (--onedir)..."
        & python -m PyInstaller @shellPyiArgs
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller для IruShell завершился с кодом $LASTEXITCODE" }
    } finally {
        Pop-Location
    }

    $shellExePath = Join-Path $stagingDistDir "IruShell\IruShell.exe"
    if (-not (Test-Path $shellExePath)) {
        throw "После сборки не найден $shellExePath"
    }

    if ($iconPath) {
        Copy-Item -LiteralPath $iconPath -Destination (Join-Path $stagingDistDir "IruShell\IruIcon.ico") -Force
    }

    [System.IO.File]::WriteAllText((Join-Path $stagingDistDir "IruShell\VERSION.txt"), $Version, [System.Text.UTF8Encoding]::new($false))
    Write-BuildInfo -TargetDir (Join-Path $stagingDistDir "IruShell") -Version $Version -Artifact "IruShell" -WebUrl $ShellWebUrl

    $shellConfig = [ordered]@{
        web_url = $ShellWebUrl
        window = [ordered]@{
            title = "ИРУ"
            width = 1200
            height = 800
            min_width = 900
            min_height = 600
        }
    }
    $shellConfigJson = $shellConfig | ConvertTo-Json -Depth 4
    [System.IO.File]::WriteAllText((Join-Path $stagingDistDir "IruShell\shell_config.json"), $shellConfigJson, [System.Text.UTF8Encoding]::new($false))

    $publishedShellDir = Publish-BuildArtifact -SourceDir (Join-Path $stagingDistDir "IruShell") -DistRoot $distDir -ArtifactName "IruShell" -Version $Version
    Write-Host ("Shell папка сборки: {0}" -f $publishedShellDir) -ForegroundColor Green

    if (-not $SkipShellZip) {
        $shellZipPath = Join-Path $distDir "IruShell.zip"
        if (Test-Path $shellZipPath) {
            Remove-Item -Force $shellZipPath
        }
        Compress-Archive -Path (Join-Path $stagingDistDir "IruShell") -DestinationPath $shellZipPath -Force
        $shellZipSize = (Get-Item $shellZipPath).Length
        Write-Host ("Shell ZIP: {0} ({1:N0} байт)" -f $shellZipPath, $shellZipSize) -ForegroundColor Green
    }

    Write-Host "IruShell не загружается в /api/agent/upload в этой ветке." -ForegroundColor DarkGray
}

try {
    if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir -ErrorAction SilentlyContinue }
    if (Test-Path $specPath) { Remove-Item -Force $specPath -ErrorAction SilentlyContinue }
    if (Test-Path $shellSpecPath) { Remove-Item -Force $shellSpecPath -ErrorAction SilentlyContinue }
    if (Test-Path $stagingRoot) { Remove-Item -Recurse -Force $stagingRoot -ErrorAction SilentlyContinue }
} catch {
    Write-Warning "Не удалось полностью очистить временные staging-артефакты."
}

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
