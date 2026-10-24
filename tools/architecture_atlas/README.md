# IRU Living Architecture — актуальный снимок

Обновлено 2026-10-23 по origin/main `712f730757ae440108fc8bcd3c3b572c57124cbc`.

1359 узлов / 3733 связей; 21 компонент / 49 механизмов. Это статический атлас исходников, а не наблюдение за production.

## Запуск на ПК

```powershell
cd 'C:\Users\russa\OneDrive\Desktop\IRU\tools\architecture_atlas'
python -m http.server 8766 --bind 127.0.0.1
```

Открыть http://127.0.0.1:8766/; обои — /wallpaper.html. Библиотеки локальные, CDN не требуется.

## Актуальное строение

- Семь подсистем — навигационные области, не независимые сервисы.
- Удалён старый PLAN/controller_pipeline. Теперь Goal/Task DAG, ревизии, зависимости и закреплённые результаты.
- Добавлены native Office, проверка сохранности, атомарная запись/chunk receipts, verified final artifacts и file transfer.
- Учтены window scope/input, memory authorization, provider/Piper и desktop/mini/mobile mode layers.
- 712f730 устраняет повтор текущего ввода и инструкций. Durable compaction отменена ee65621 и в карте не отображается как действующий механизм.

## Управление

Выбор узла: фиолетовые входящие, оранжевые исходящие. Над графом фильтр Все/Входящие/Исходящие. Анализ → Направления всех связей включает полный граф и общую окраску; Убрать подсветку выключает её. Декоративные импульсы не являются runtime evidence.

Анализ → Важные узлы связности: 25 описаний с метриками, условными рисками и исходниками офлайн. Полный текст: CRITICAL-REVIEW.md.

## Обновление и происхождение

data/update-manifest.json содержит SHA, последние коммиты и добавленные/удалённые IDs. data/connectivity-analysis.json — отдельные статический и агрегированный анализы. data/critical-review.json — описания с SHA256 исходников.

Генерация читает только экспортированный commit; production-модули не импортируются и не исполняются. scripts/extract.py поддерживает --source-root и --manifest. scripts/critical_review.py пересчитывает связность; scripts/refresh_content.py обновляет разбор из закреплённого снимка.

## Ограничения

Динамические callbacks/registry/event dispatch и JS call graph не восстановлены. CSS/HTML/JS import/link/script — явные подключения, не фактическая трасса выполнения. Непривязанные реализации явно помечены. Высокая степень и SCC не доказывают runtime SPOF. Старые V0.* отчёты — исторические, их числа не описывают этот снимок.

## Помощник DeepSeek

Для чата с управлением картой используйте `python assistant/server.py --port 8774` и кнопку «Помощник». Настройка ключа, возможности и границы: [ASSISTANT.md](ASSISTANT.md). Прежний статический запуск сохраняет карту, но не LLM API.
