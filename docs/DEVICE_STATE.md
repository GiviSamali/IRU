# Device State и Device Passport

Device Passport — компактное представление текущего и cached состояния устройства. Он объединяет activation, runtime, health, identity, snapshot и hardware.

## Паспорт устройства

Паспорт включает:

- activation summary;
- runtime summary;
- health summary;
- identity;
- state snapshot summary;
- hardware summary;
- GPU summary;
- context handles.

Полные receipts и snapshots не должны по умолчанию попадать в LLM context. Для этого используется lazy context и compact summaries.

## Agent-owned state cache

Источник правды для локального состояния — агент. Сервер координирует выполнение и держит временное зеркало.

```
Agent IRU_HOME/state
  -> registration payload / command result
  -> server connected-device mirror
  -> UI Device Passport
```

Сервер может сохранять summaries в DB, но не владеет полным локальным state cache.

## device.refresh_state

`device.refresh_state` выполняется на агенте:

1. собирает snapshot локально;
2. собирает identity receipt;
3. определяет health/status;
4. сохраняет `state/state_snapshot.json`;
5. обновляет `state/device_passport.json`;
6. возвращает summary серверу.

Если observed identity не совпадает с registered identity, результат должен показывать mismatch/routing problem, а не делать выводы по чужому устройству.

## Что собирается

- CPU;
- RAM;
- Disk;
- Processes;
- Uptime;
- GPU;
- OS;
- identity.

Конкретные поля зависят от платформы и текущей agent-side реализации.

## Sources

В UI/API состояние должно иметь source:

- `live` — свежий snapshot текущего подключения;
- `agent_cache` — cached passport/snapshot, присланный агентом при connect/reconnect;
- `missing` — данных нет.

`agent_cache` полезен для восстановления UI после reload, но не является свежим live observation.

## UI behavior

После обновления страницы UI показывает последний agent cache, если агент подключен и прислал cached passport. Device Passport также показывает источник snapshot и свежесть данных.

Для текущих утверждений вроде "сейчас открыт процесс" или "сейчас доступна GPU" предпочтителен fresh `device.refresh_state`.

## Ограничение

Если устройство offline и сервер перезапустился, сервер не может достать локальный cache до reconnect агента. В этом случае он может показать только сохраненные server-side summaries, если они есть, или `missing`.
