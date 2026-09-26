# Выпуск и проверка Windows-агента

Версия сервера, подпись systemd «ИРУ v3.4», версия интерфейса и версия агента —
разные значения. Релиз агента определяется **содержимым ZIP**, а не ручной правкой JSON.

## Причина зацикленного обновления

26 сентября 2026 проверка `/api/agent/download` через домен и IP `178.20.42.164`
показала одинаковый ZIP: VERSION.txt и BUILD_INFO.json содержали **3.7**, а
`/api/agent/version` объявлял **3.13.4**. ZIP был собран из `02747d0`.
Старый сервер доверял query-параметру `version`; старый агент не проверял
версию внутри архива, поэтому повторно устанавливал старую сборку.

`bc26593` добавляет `-UploadResolveIp`: curl соединяется с указанным IP,
сохраняя имя HTTPS-хоста и проверку сертификата. Он **не меняет DNS**,
server_url в агентах или сервер, который обслуживает остальных пользователей.
HEAD (`curl -I`) не заменяет проверку GET маршрута скачивания.

## Контракт релиза

- Сборка: `IruAgent/IruAgent.exe`, `IruAgent/VERSION.txt`, `IruAgent/BUILD_INFO.json`
  в одном ZIP. VERSION.txt и BUILD_INFO.version должны совпадать с `-Version`.
- Сервер принимает только проверенный ZIP: сверяет структуру, версию, CRC,
  BUILD_INFO (если присутствует), вычисляет размер и SHA-256.
- ZIP хранится под неизменяемым именем `IruAgent-<version>-<sha256>.zip`.
  `server/updates/release.json` публикуется атомарно после записи архива.
- `release.json` и архивы — серверные данные, исключённые из Git.
  Старый отслеживаемый `version.json` — только совместимый резервный источник:
  используется при отсутствии release.json и обязательно проверяется по ZIP.
  Новая загрузка его не изменяет. Не редактировать номер версии вручную.
- `/api/agent/version`, `/api/agent/download` и старый `/api/download_agent`
  используют один релиз; папка `/opt/iru/app/exe` больше не источник скачивания.
- Ссылка содержит ожидаемые версию и SHA-256. Если релиз сменился между
  проверкой и скачиванием, сервер отвечает 409; следующая проверка получает новую ссылку.
  Метаданные и скачивание имеют `Cache-Control: no-store`.
- Отсутствующий/несогласованный релиз даёт 503 вместо объявления ложной версии.
  Повреждённый архив не выдаётся. Загрузка некорректного ZIP не меняет предыдущий релиз.
- Повторно загрузить те же байты можно. Другой ZIP с тем же номером и понижение
  валидного релиза запрещены: для пересборки увеличить версию.
- Новый агент проверяет SHA-256/размер, если они объявлены, и VERSION.txt внутри
  ZIP **до запуска установщика и остановки работающего процесса**.
  Для старых серверов ZIP без SHA-256 всё равно проходит сверку VERSION.txt.
  Legacy EXE остаётся совместимым на клиенте, но новый сервер публикует только ZIP.

## Исправление текущего сервера

1. Сначала обновить сервер из `codex/deeptalk-integration` по [BRANCHES.md](BRANCHES.md).
   Если `version.json` изменён, временно сохранить только его через `git stash push
   -- server/updates/version.json`, обновить код без рестарта, выполнить `git stash pop`,
   затем перезапустить iru. Не сбрасывать локальные изменения вслепую.
2. До загрузки корректного ZIP ответ 503 для старого несогласованного релиза ожидаем:
   это останавливает раздачу неправильной сборки, а не исправляет её номер.
3. На Windows получить этот же код, собрать **новую версию**, например 3.13.5,
   чтобы в неё попали проверки клиента. Одного обновления сервера недостаточно
   для изменения уже установленных EXE. Не переименовывать старую сборку в новую.
4. Загрузить штатным скриптом ниже и убедиться в совпадении SHA-256 и версии.
5. После успешной публикации release.json сохранить старый version.json в резервную
   копию **вне репозитория**, затем выполнить `git restore -- server/updates/version.json`.
   Это допустимо только после проверки нового релиза: активные данные уже в release.json.
   Следующие загрузки не пачкают отслеживаемые файлы и не блокируют update_server.py.

## Обычный выпуск

В Windows PowerShell, из каталога IRU с обновлённым кодом:

```powershell
# IRU_ADMIN_TOKEN задан в окружении локально; не сохранять его в Git.
.\deploy\build_windows.ps1 -Version 3.13.5 -Server https://irumode.ru
```

Скрипт читает VERSION.txt именно из загружаемого ZIP, вычисляет SHA-256,
проверяет подтверждение upload и затем `/api/agent/version` на том же сервере.
Если сервер ещё старый и не возвращает SHA-256, скрипт завершится ошибкой
проверки: сначала обновить сервер. Новую сборку не считать опубликованной до проверки.

Без загрузки: добавить `-SkipUpload`. Для Agent Shell использовать `-BuildShell`;
IruShell.zip не загружается как обновление IruAgent. Не выпускать два релиза одновременно.

## Переезд до смены DNS

```powershell
.\deploy\build_windows.ps1 -Version 3.13.5 -Server https://irumode.ru -UploadResolveIp 178.20.42.164
curl.exe --resolve irumode.ru:443:178.20.42.164 -fsS https://irumode.ru/api/agent/version
curl.exe -fsS https://irumode.ru/api/agent/version
```

Использовать `curl.exe`, поскольку `curl` в Windows PowerShell может быть alias
Invoke-WebRequest. IP выше — адрес этого переезда, не постоянная настройка.
Если DNS ещё старый, ответы могут различаться. После переключения DNS сверить
**version и sha256** через оба адреса. Одного совпадения version недостаточно.
Переносить release.json и указанный в нём ZIP совместно. Не копировать старый
IruAgent.zip поверх опубликованного архива. При миграции применять тот же проверенный
ZIP через upload предпочтительнее ручного копирования; сертификат TLS не отключать.

## Проверка скачиваемых байтов

```powershell
$release = (curl.exe -fsS https://irumode.ru/api/agent/version) | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Version request failed' }
$zip = Join-Path $env:TEMP ('iru-release-check-' + [guid]::NewGuid().ToString('N') + '.zip')
curl.exe -fsS ("https://irumode.ru" + $release.download_url) -o $zip
if ($LASTEXITCODE -ne 0) { throw 'Download failed' }
if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $release.sha256) { throw 'SHA-256 mismatch' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($zip)
try {
    $entry = $archive.GetEntry('IruAgent/VERSION.txt')
    if (-not $entry) { $entry = $archive.GetEntry('VERSION.txt') }
    $reader = [IO.StreamReader]::new($entry.Open())
    try { $inside = $reader.ReadToEnd().Trim() } finally { $reader.Dispose() }
    if ($inside -ne $release.version) { throw 'ZIP version mismatch' }
    Write-Host "Verified $inside / $($release.sha256)"
} finally { $archive.Dispose() }
Remove-Item -LiteralPath $zip
```

Эта проверка не запускает EXE. После установки проверить VERSION.txt рядом с
**реально запущенным** IruAgent.exe и отображаемую версию устройства. Ярлык
автозагрузки не должен вести в старую папку dist/распаковки. Если контрольные суммы
на сервере корректны, а версия устройства старая, проверить путь процесса/ярлыка.

## Regression checks

```text
python -m pytest -q tests/test_agent_release.py tests/test_windows_build_script.py
```

Тесты используют синтетические ZIP и не запускают Windows-установщик.
Реальный smoke выпуска: сборка → upload → GET версии → скачивание/хэш →
установка на тестовом устройстве → повторная проверка без повторной установки.
