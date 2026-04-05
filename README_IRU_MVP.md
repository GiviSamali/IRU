# ИРУ — Интеллектуальный Режим Управления (MVP)

Этот репозиторий — MVP ядра ИРУ: детерминированный агент на ПК + сервер c HTTP/WebSocket‑интерфейсом, через который можно управлять машиной по формальному протоколу `device_id + action + params`.

ИРУ задуман как «низкоуровневый исполнитель», поверх которого может работать любой «мозг» (модель, скрипт, внешний сервис), не зная деталей ОС.

---

## Архитектура

```text
<project_root>/
│
├── agent/                  # Локальный агент на ПК
│   │   agent.py            # WebSocket-клиент, выполняет actions
│   │   config.json         # device_id + URL сервера
│   │
│   └── actions/            # Детерминированные действия над системой
│       │   apps.py         # open_app
│       │   files.py        # find_file, list_dir, open_path
│       │   downloads.py    # download_file, get_file_content
│       │   __init__.py     # реестр ACTIONS
│
└── server/                 # Центральный сервер ИРУ
    │   main.py             # FastAPI + WebSocket для агентов
    │   index.html          # MVP-веб-интерфейс
    │   iru_client.py       # (опционально) Python-клиент для /command
```

### Поток команды

1. Внешний клиент (браузер, скрипт, другая модель) отправляет HTTP‑запрос:

   ```http
   POST /command
   Content-Type: application/json

   {
     "device_id": "PC_HOME",
     "action": "open_app",
     "params": { "name": "Steam" }
   }
   ```

2. `server/main.py` кладёт команду в очередь конкретного `device_id` и ждёт результат по WebSocket.
3. `agent/agent.py` получает команду, находит функцию в `ACTIONS[action]`, выполняет её и присылает результат.
4. Сервер возвращает HTTP‑ответ с JSON.

---

## Протокол команды

### Вход (HTTP → сервер)

```json
{
  "device_id": "PC_HOME",
  "action": "find_file",
  "params": {
    "name_part": ".pdf",
    "base": "C:\Users\russa\OneDrive\Desktop",
    "max_results": 10
  }
}
```

- `device_id` — логическое имя устройства (из `agent/config.json`).  
- `action` — имя действия, известное агенту (`ACTIONS`).  
- `params` — объект с параметрами, структура зависит от `action`.

### Выход (сервер → клиент)

Успех:

```json
{
  "status": "ok",
  "command_id": "cmd-xxxxxxx",
  "result": {
    "id": "cmd-xxxxxxx",
    "status": "ok",
    "result": { "...": "action-specific" }
  }
}
```

Ошибка (пример):

```json
{
  "status": "error",
  "error": "device_offline",
  "command_id": "cmd-xxxxxxx"
}
```

Внутри `result` агент также может вернуть `status: "error"` и строку `error` для ошибок исполнения (FileNotFound и т.п.).

---

## Набор базовых actions (язык ядра)

### 1. `open_app`

Запустить известное приложение на ПК.

**Params:**

```json
{ "name": "Steam" }
```

**Result:**

```json
{
  "message": "Steam started",
  "path": "C:\Program Files (x86)\Steam\Steam.exe"
}
```

---

### 2. `find_file`

Поиск файлов по имени/подстроке.

**Params:**

```json
{
  "name_part": ".pdf",
  "base": "C:\Users\russa\OneDrive\Desktop",
  "max_results": 10
}
```

Если `base` не указан — используется дефолтный Desktop (OneDrive/Desktop, затем обычный Desktop).

**Result:**

```json
{
  "base": "C:\Users\russa\OneDrive\Desktop",
  "query": ".pdf",
  "count": 3,
  "files": [
    "C:\Users\russa\OneDrive\Desktop\a.pdf",
    "C:\Users\russa\OneDrive\Desktop\b.pdf",
    "..."
  ]
}
```

---

### 3. `list_dir`

Содержимое директории (список файлов и папок).

**Params:**

```json
{ "path": "C:\Users\russa\OneDrive\Desktop" }
```

Если `path` не указан или пустой — используется дефолтный Desktop.

**Result:**

```json
{
  "path": "C:\Users\russa\OneDrive\Desktop",
  "files": ["C:\Users\russa\OneDrive\Desktop\a.pdf", "..."],
  "dirs":  ["C:\Users\russa\OneDrive\Desktop\Folder1", "..."],
  "files_count": 10,
  "dirs_count": 3
}
```

---

### 4. `open_path`

Открыть путь стандартным способом в ОС (файл/папка).

**Params:**

```json
{ "path": "C:\Users\russa\OneDrive\Desktop" }
```

**Result:**

```json
{
  "message": "opened",
  "path": "C:\Users\russa\OneDrive\Desktop"
}
```

---

### 5. `download_file`

Зарегистрировать файл для скачивания, получить токен.

**Params:**

```json
{ "path": "C:\Users\russa\OneDrive\Desktop\01236.pdf" }
```

**Result:**

```json
{
  "token": "file-af60a869",
  "name": "01236.pdf",
  "size": 2098420,
  "content_type": "application/pdf"
}
```

Токен живёт в памяти агента и используется для последующего скачивания.

---

### 6. `get_file_content`

Отдать содержимое файла по токену (для `/files/{token}`).

**Params:**

```json
{ "token": "file-af60a869" }
```

**Result:**

```json
{
  "name": "01236.pdf",
  "size": 2098420,
  "content_type": "application/pdf",
  "data_base64": "<base64-данные>"
}
```

Сервер декодирует `data_base64` и отдаёт файл как HTTP‑ответ.

---

## Дополнительные HTTP‑эндпоинты сервера

### `POST /command`

Универсальная точка входа для протокола `device_id + action + params`.

Используется:

- фронтом (`index.html`) через `fetch('/command', ...)`;
- любыми внешними клиентами (скрипты, другие сервисы).

### `GET /files/{token}`

Скачивание файла по токену.

- Сервер по токену инициирует команду `get_file_content` к агенту.
- Агент возвращает содержимое в base64.
- Сервер отдаёт файл с корректным `Content-Type` и `Content-Disposition`.

Пример URL (через ngrok):

```text
https://<ngrok-id>.ngrok-free.app/files/file-af60a869
```

---

## Веб‑интерфейс (MVP)

`server/index.html` — одностраничный фронт для демонстрации ИРУ:

- Выбор `device_id`, `action`.
- Формы для параметров под каждое действие (`open_app`, `find_file`, `list_dir`, `open_path`, `download_file`).
- Кнопки‑пресеты:
  - «Открыть Steam»;
  - «Найти PDF на рабочем столе»;
  - «Показать Desktop (list_dir)»;
  - «Скачать файл (получить ссылку)».
- Вывод:
  - сырой JSON‑ответ;
  - для `list_dir` — список файлов/папок;
  - для `download_file` — ссылка `/files/{token}` для скачивания.

---

## Зачем это для ИРУ

Этот MVP фиксирует **язык низкого уровня** ИРУ:

- Каждое действие:
  - имеет чёткий контракт вход/выход;
  - детерминированно воздействует на систему;
  - ограничено по «зоне ответственности» (файлы/приложения).

Дальнейшие шаги:

- Добавление слоя «управляющего кода» (планировщика), который из целей собирает программы из этих примитивов.
- Экспонирование того же протокола в обучающую/управляющую модель, чтобы она училась действовать в рамках этого языка, а не напрямую «тыкать мышкой».

ИРУ в этом виде уже можно:
- показывать как живой пример «управляющего режима» для ПК;
- использовать как полигон для обучения моделей/людей работе с формальным протоколом управления системой.
