# Голос после долгого простоя

Ветка: codex/agentshell-webview. База: 8d4cf10c86507d9cd8c473a86861abad01d74b1a. Дата: 09.10.2026.

Подтверждено в коде общего сайта: SpeechRecognition error=network вызывал reset и полностью выключал voice session. При no-speech ожидалось только onend; потерянное событие завершения или зависший старт оставляли ссылку recognition, запрещавшую новое start. Десятисекундное окно разговора само по себе не выключает микрофон: после него требуется wake word.

Изменения в ui/js/voice.js:

- network/no-speech/неожиданный aborted перезапускают transport, сохраняя voice session и wake/sleep состояние;
- network и временный InvalidStateError/NetworkError при start повторяются с backoff 1/2/4/8/15 секунд, без максимального срока ожидания;
- отсутствие start дольше 10 секунд и отсутствие speech/result activity дольше 90 секунд обновляют recognition даже без onend;
- speech/result activity продлевает health deadline, чтобы периодическое обновление не обрывало обычную продолжающуюся диктовку;
- online, focus, visibilitychange и browser resume восстанавливают отсутствующую/просроченную сессию после приостановки таймеров;
- остановка, TTS и запрещённый доступ очищают оба таймера; stale callbacks игнорируются. Повторный listen(true) не обходит backoff;
- интерфейс честно показывает «Восстанавливаю микрофон…», пока идёт reconnect;
- not-allowed/service-not-allowed/audio-capture/unsupported language дают понятное сообщение и требуют действия пользователя, без обхода разрешений.

Сохранены один voice controller, один текущий recognition run, Android single-utterance/deduplication, ожидание wake word, sleep word, существующие опасные confirmations и TTS. Agent, WebView2 host, Worker и серверный protocol не менялись. Запись аудио, хранение транскриптов и новый STT-сервис не добавлялись.

Файлы: ui/js/voice.js, tests/voice-recognition.test.cjs, tests/smart-ui-browser.test.cjs, этот документ.

Проверки:

- node --test tests/voice-recognition.test.cjs tests/voice-session.test.cjs — 91 passed до добавления отдельной проверки browser resume;
- node --test --test-name-pattern='five-minute idle' tests/smart-ui-browser.test.cjs — 2 passed;
- node --test tests/*.test.cjs — 180 passed, 0 failed после всех изменений;
- node --check для изменённых JS/CJS и git diff --check — прошли.

Добавлены 17 регрессий: 30 минут simulated silence desktop/Android с последующим wake, временные ошибки без onend, длительный outage/backoff, отказ разрешения, зависший start, stale callbacks, выключение/отмена retry, возврат/freeze/resume, сохранение sleep и активная диктовка. Реальная страница в headless Edge с desktop/Android UA проверяет пять минут виртуального простоя, network reconnect, отсутствие пустого сообщения от «ИРУ.» и ровно одну отправку после wake. Речь и сеть STT подменены: качество/доступность настоящего сервиса на ПК пользователя этим не подтверждены. Python и native sources не изменены; их полный прогон не повторялся.

Ручная проверка после deploy: включить голос один раз, оставить открытую страницу на 5 и затем 15 минут без речи; сказать «Иру, проверка связи». Повторить в IruAgent, desktop-браузере и Android; выключить/включить сеть и дождаться возвращения статуса ожидания. Проверить «усни», последующее пробуждение и отключение кнопкой: после отключения микрофон сам не включается. Пересборка IruAgent для этого изменения не требуется — он открывает обновлённый сайт.

Ограничение: пока ОС заморозила/выгрузила страницу или браузер, JavaScript и microphone capture не могут гарантированно работать. Для замороженной, но сохранённой страницы предусмотрено восстановление после возврата. После выгрузки/перезагрузки сохраняется обычное явное включение голоса. Нельзя обещать always-on wake на заблокированном Android через веб-страницу; это отдельная native background capability.

Справка: [SpeechRecognition end](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/end_event), [SpeechRecognition error](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/error_event), [Chrome page lifecycle: frozen/discarded](https://developer.chrome.com/docs/web-platform/page-lifecycle-api).
