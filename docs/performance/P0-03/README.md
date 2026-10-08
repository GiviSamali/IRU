# IRU — P0-03: Performance Baseline

> Пакет сохранён в репозитории по отдельной команде commit/push. Изменены только материалы аудита; production instrumentation не применено. HEAD и чистота ниже относятся к моменту измерений.

Файлы: [числовые метрики](IRU-P0-03-metrics.json), [harness](iru_p0_03_baseline.py), [проверки](baseline_checks.py), [неприменённый diff](IRU-P0-03-proposed-instrumentation.diff).

Повторение из корня проекта в test environment:

```sh
python -m pytest -q docs/performance/P0-03/baseline_checks.py
python docs/performance/P0-03/iru_p0_03_baseline.py --samples 30 --calibrate --output /tmp/iru-p0-03-metrics.json
```

На Windows замените output на путь в своём временном каталоге. Harness и проверки сами определяют корень checkout; исходники IRU не меняются. Для исследования snapshot обязателен контрольный commit a55512b либо его неизменённые runtime sources. Запускайте отдельным Python процессом с тестовыми dependencies. Файл baseline_checks.py не входит в обычный pytest discovery; запускается явно. Предложенный diff проверяется в памяти и не применяется к файлам.


Дата: 08.10.2026. Измерение, без оптимизации.

Подтверждены объёмы контекста и число вызовов управляемых сценариев, повторная сборка manifests/profiles, полное чтение фактов перед bounded selection и при polling. Production latency, реальные tokens и стоимость неизвестны.

## A. Исходное состояние и HEAD


GiviSamali/IRU, ветка `codex/agentshell-webview`, HEAD `a55512b2e21a6448689bbdeda4d35ff6b4136589` совпадает с контрольным commit. Фактический checkout: `C:/Users/russa/OneDrive/Desktop/ИРУ/IRU/`; каталог `C:/Users/russa/OneDrive/Desktop/IRU` из окружения не содержит Git checkout.

Tracked diff пуст; исходники IRU не менялись. Посторонний untracked `codex_self_extension_tmp/` сохранён. Commit, push, PR, migrations отсутствуют. Измерительные артефакты находятся вне репозитория. Изучены controller/non-pipeline/PLAN/runtime, prompts/shared context, database, usage/lifecycle, tasks API, auditor, voice и тесты. Старые исправленные memory/owner/onboarding проблемы не объявляются текущими дефектами.


## B. Существующие метрики

| Механизм | Доступно | Недостаёт |
| --- | --- | --- |
| llm_usage_events | task/chat/user IDs, route/phase/provider/model, usage/cache, estimated cost, request status, metadata | HTTP durations/attempts, serialized sizes и состав контекста |
| run_journal lifecycle | task_id, request start/finish, elapsed_ms на событиях, classification/controller/tool/recovery/answer events, bounded trace | отдельные DB/context/HTTP/auditor/persistence durations |
| Tool journal | current-run step_id, status, target, summary, evidence/basis | самостоятельные duration |
| Voice cache | повторное использование `_voice_brief` | STT/UI/playback durations |


[server/llm_usage.py:39](../../../server/llm_usage.py#L39): missing usage превращается в нули. В baseline tokens/cost экспортируются как null/unknown, а не как реальные нули. [server/controller.py:298](../../../server/controller.py#L298): retry может быть внутри одного logical completion; количество usage events не всегда равно HTTP attempts. [server/run_journal.py:34](../../../server/run_journal.py#L34): расширять следует существующий trace и metadata, без второго telemetry store.

## C. Непосредственные измерения и границы

21 вариант S1–S10 × 30 зачётных запусков = 630 запросов; по одному warmup исключено. Ещё 30 пар recording on/off для четырёх fixtures = 240 запусков. S9 tool correction повторён отдельно после уточнения fixture; в итоговом JSON только последние 30 его измерений.

Оригинальные run_nl_task/controller/classifier/worker/guards/auditor/SQL helpers/review/polling/voice исполняются с fake agent и httpx.MockTransport. Модели имеют штатные имена Flash/Pro, auditor включён и действительно вызывает fake provider. Новых LLM-вызовов в IRU не добавлено.

БД временная, данные синтетические, devices owned/fake. Перед повтором удаляются новые messages, command memory, tasks и usage; caches/markers сбрасываются. PLAN review одобряется существующим endpoint с fake auth, без ожидания человека. Assertion требует прохождения worker и validated answer.text, а не только done.

- request_elapsed_ms: fixture acceptance DB write → окончание runtime. Предшествующие HTTP auth/quota/scheduling не измерены.
- context_preparation_ms: сумма внешних context builders без повторного прибавления вложенных durations. Предварительные DB reads имеют отдельные spans.
- classification_elapsed_ms: функция classifier вместе с локальной записью usage.
- llm_http_elapsed_ms: сумма post всех LLM фаз, включая classifier/auditor/brief. В mock callback входит overhead учёта payload, измеренный отдельно.
- tool_execution_ms/tool_call_count: dispatch к fake agent; terminal answer.*, validation rejection и auditor не являются agent round trips. Это не число всех элементов journal.
- result_persistence_ms: обе add_message, acceptance и ответ. runtime_message_persistence_ms — только финальный ответ; usage writes отдельно.
- Polling и voice запускаются после завершения и не входят в request_elapsed_ms.

Сохранены numeric bytes/chars, counts, route/phase/model, SQL rows/helper calls, состав input, builder output sizes, bounded memory output, polling response sizes. Prompt/messages/paths/stdout/DOM/credentials в экспорт не записываются. Реальных действий и платных LLM/STT/TTS нет; synthetic Ogg bytes не являются настоящей озвучкой.


## D. LLM calls и контекст

HTTP completions на запрос; Main у PLAN включает planner и workers, Other — repair/brief.

| Сценарий | Classification | Main | Auditor | Other | Total | Agent dispatch |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | 1 | 1 | 1 | 0 | 3 | 0 |
| S2 | 1 | 1 | 1 | 0 | 3 | 0 |
| S3 | 1 | 2 | 1 | 0 | 4 | 1 |
| S4 | 1 | 1 | 1 | 0 | 3 | 0 |
| S5_50 | 1 | 1 | 1 | 0 | 3 | 0 |
| S5_500 | 1 | 1 | 1 | 0 | 3 | 0 |
| S5_5000 | 1 | 1 | 1 | 0 | 3 | 0 |
| S6_1 | 1 | 1 | 1 | 0 | 3 | 0 |
| S6_5 | 1 | 1 | 1 | 0 | 3 | 0 |
| S6_20 | 1 | 1 | 1 | 0 | 3 | 0 |
| S7 | 0 | 7 | 3 | 0 | 10 | 3 |
| S8_short | 1 | 1 | 1 | 0 | 3 | 0 |
| S8_long | 1 | 1 | 1 | 1 | 4 | 0 |
| S9_correction | 1 | 2 | 1 | 0 | 4 | 0 |
| S9_repair | 1 | 20 | 1 | 1 | 23 | 0 |
| S9_recovery | 1 | 3 | 1 | 0 | 5 | 2 |
| S9_tool_correction | 1 | 3 | 1 | 0 | 5 | 1 |
| S9_auditor_correction | 1 | 2 | 2 | 0 | 5 | 0 |
| S10_100x100 | 1 | 1 | 1 | 0 | 3 | 0 |
| S10_1000x100 | 1 | 1 | 1 | 0 | 3 | 0 |
| S10_1000x2000 | 1 | 1 | 1 | 0 | 3 | 0 |

S9_correction: один raw assistant response, затем terminal. Tool correction: отсутствует обязательный command, deterministic rejection, исправленное действие и terminal. Recovery: failed execute, другая исправленная команда и terminal. Auditor correction: первый answer отклонён, следующий принят. Repair: 20 raw responses, затем существующий answer-only repair и auditor. Это принудительно вызванные ветви, не их частота в production. S4 измеряет наличие artifact context, не качество настоящего followup.

Ниже — сумма UTF-8 bytes всех requests первого зачётного запуска, не tokens и не peak одного request. Whole serialized input измерен точно; в JSON есть каждый request и peak по всем повторам.


| Сценарий | System* | Schemas | History | Memory | Tool results | Прочее** | Total bytes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | 29705 | 25895 | 0 | 0 | 0 | 5041 | 60641 |
| S2 | 29705 | 25895 | 0 | 0 | 0 | 5155 | 60755 |
| S3 | 58402 | 51790 | 0 | 0 | 363 | 10128 | 120683 |
| S4 | 29705 | 25895 | 3520 | 0 | 0 | 5662 | 64782 |
| S5_50 | 29705 | 25895 | 21600 | 0 | 0 | 6701 | 83901 |
| S5_500 | 29705 | 25895 | 21658 | 0 | 0 | 6700 | 83958 |
| S5_5000 | 29705 | 25895 | 21707 | 0 | 0 | 6701 | 84008 |
| S6_1 | 29705 | 25895 | 0 | 0 | 0 | 5041 | 60641 |
| S6_5 | 29705 | 25895 | 0 | 0 | 0 | 9510 | 65110 |
| S6_20 | 29705 | 25895 | 0 | 0 | 0 | 26390 | 81990 |
| S7 | 89827 | 137250 | 2968 | 2631 | 1089 | 35939 | 269704 |
| S8_short | 29705 | 25895 | 0 | 0 | 0 | 5041 | 60641 |
| S8_long | 31484 | 25895 | 0 | 0 | 0 | 7726 | 65105 |
| S9_correction | 58402 | 51790 | 0 | 0 | 0 | 9489 | 119681 |
| S9_repair | 603645 | 518766 | 0 | 0 | 0 | 116242 | 1238653 |
| S9_recovery | 87099 | 77685 | 0 | 0 | 1287 | 15582 | 181653 |
| S9_tool_correction | 87099 | 77685 | 0 | 0 | 1261 | 15701 | 181746 |
| S9_auditor_correction | 58917 | 51790 | 0 | 0 | 0 | 10109 | 120816 |
| S10_100x100 | 29705 | 25895 | 0 | 1885 | 0 | 5203 | 62688 |
| S10_1000x100 | 29705 | 25895 | 0 | 1886 | 0 | 5203 | 62689 |
| S10_1000x2000 | 29705 | 25895 | 0 | 177 | 0 | 5063 | 60840 |

* System = source-attributed instruction literals + unmatched system residual. Остаток содержит literals/labels/dynamic fields; это не доказанный exclusively fixed context. ** Прочее = device/artifact/handoff/current turn/corrections/auditor journal/answer payload/JSON overhead. В JSON раздельно. Data-only blocks измерены вместе с boundary labels; artifact/conversation blocks включают постоянные пояснения. Числа показывают стоимость emitted блока, не только его сырых данных.


| Компонент bytes | S1 | S3 | S7 | S10 1000×2000 |
| --- | --- | --- | --- | --- |
| fixed_instructions_bytes | 26989 | 52970 | 85581 | 26989 |
| system_residual_bytes | 2716 | 5432 | 4246 | 2716 |
| device_bytes | 2765 | 5526 | 17591 | 2765 |
| artifact_bytes | 866 | 1732 | 5082 | 866 |
| history_bytes | 0 | 0 | 2968 | 0 |
| memory_bytes | 0 | 0 | 2631 | 177 |
| handoff_bytes | 0 | 0 | 4038 | 0 |
| tool_results_bytes | 0 | 363 | 1089 | 0 |
| journal_bytes | 2 | 365 | 1095 | 2 |
| answer_payload_bytes | 251 | 263 | 789 | 251 |
| current_turn_and_corrections_bytes | 101 | 177 | 1492 | 101 |
| schemas_bytes | 25895 | 51790 | 137250 | 25895 |
| json_metadata_overhead_bytes | 1056 | 2065 | 5852 | 1078 |

Отдельные completion requests:

| Сценарий | Route / phase | Model | Messages | Schemas | Input bytes | Input chars | Thinking |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | classification / classification | deepseek-v4-flash | 2 | 0 | 655 | 443 | disabled |
| S1 | non_pipeline / non_pipeline.iteration.1 | deepseek-v4-flash | 2 | 39 | 58928 | 50245 | disabled |
| S1 | non_pipeline / answer_auditor | deepseek-v4-flash | 2 | 0 | 1058 | 1034 | disabled |
| S3 | classification / classification | deepseek-v4-flash | 2 | 0 | 671 | 454 | disabled |
| S3 | non_pipeline / non_pipeline.iteration.1 | deepseek-v4-flash | 2 | 39 | 58942 | 50254 | disabled |
| S3 | non_pipeline / non_pipeline.iteration.2 | deepseek-v4-flash | 4 | 39 | 59579 | 50891 | disabled |
| S3 | non_pipeline / answer_auditor | deepseek-v4-flash | 2 | 0 | 1491 | 1462 | disabled |
| S7 | pipeline / pipeline.plan | deepseek-v4-pro | 2 | 0 | 15497 | 11713 | disabled |
| S7 | pipeline / pipeline.worker.step_1.iteration.1 | deepseek-v4-pro | 3 | 31 | 40284 | 34579 | enabled / high |
| S7 | pipeline / pipeline.worker.step_1.iteration.2 | deepseek-v4-pro | 5 | 31 | 40892 | 35187 | enabled / high |
| S7 | pipeline / pipeline.worker.step_1.answer_auditor | deepseek-v4-flash | 2 | 0 | 1603 | 1560 | disabled |
| S7 | pipeline / pipeline.worker.step_2.iteration.1 | deepseek-v4-pro | 3 | 31 | 41297 | 35598 | enabled / high |
| S7 | pipeline / pipeline.worker.step_2.iteration.2 | deepseek-v4-pro | 5 | 31 | 41905 | 36206 | enabled / high |
| S7 | pipeline / pipeline.worker.step_2.answer_auditor | deepseek-v4-flash | 2 | 0 | 1603 | 1560 | disabled |
| S7 | pipeline / pipeline.worker.step_3.iteration.1 | deepseek-v4-pro | 3 | 31 | 42206 | 36471 | enabled / high |
| S7 | pipeline / pipeline.worker.step_3.iteration.2 | deepseek-v4-pro | 5 | 31 | 42814 | 37079 | enabled / high |
| S7 | pipeline / pipeline.worker.step_3.answer_auditor | deepseek-v4-flash | 2 | 0 | 1603 | 1560 | disabled |
| S8_long | classification / classification | deepseek-v4-flash | 2 | 0 | 693 | 464 | disabled |
| S8_long | non_pipeline / non_pipeline.iteration.1 | deepseek-v4-flash | 2 | 39 | 58965 | 50265 | disabled |
| S8_long | non_pipeline / answer_auditor | deepseek-v4-flash | 2 | 0 | 2218 | 1655 | disabled |
| S8_long | voice / voice_brief | deepseek-v4-flash | 2 | 0 | 3229 | 1882 | disabled |

На S7 happy path planner 1 + workers 6 + auditors 3 = 10. Final summary — deterministic pipeline_step_report, без дополнительного LLM. [server/controller_pipeline.py:2272](../../../server/controller_pipeline.py#L2272); альтернативная pipeline.final и auditor существуют: [server/controller_pipeline.py:2313](../../../server/controller_pipeline.py#L2313), здесь не замерены. Baseline phase classification соответствует usage phase classify_task_complexity.

S9_repair: input main растёт 58927→61055 bytes, total 1238653 bytes. Причина — corrections и повторные system/schemas. Повторная передача не равна полной повторной оплате: cache неизвестен.

Реальные prompt/completion/reasoning/cached tokens и денежная стоимость во всех fixtures unknown. Tokenizer не использован, bytes→tokens не оценивались. Реальный total input = sum(prompt_tokens) usage-known вызовов; cache-hit часть уже входит в prompt total, её не прибавляют второй раз. Unknown вызовы не равны нулю. Цены в коде не выдаются за проверенный внешний тариф.

Карта обнаруженных LLM-фаз (неизмеренные ветви не считаются нулевой стоимостью):

| Phase | Где вызывается | Покрытие baseline |
| --- | --- | --- |
| classify_task_complexity | controller.py:108, прямой HTTP | S1–S6/S8–S10; в harness label classification |
| non_pipeline.iteration.N | controller_non_pipeline.py:391 | happy/correction/recovery/exhaustion |
| answer_auditor | controller_non_pipeline.py:522 | happy и rejection |
| answer_repair / answer_repair.auditor | controller_non_pipeline.py:1268, answer_repair.py:65/90 | S9_repair |
| window_control.iteration.N | controller_non_pipeline.py:391 | source inspection, latency не измерена |
| browser_bridge.iteration.N / answer_auditor / answer_repair(.auditor) | controller_non_pipeline.py:391/522/1268 | source inspection, latency не измерена |
| onboarding | controller_onboarding.py:67–104, прямой HTTP, до 4 iterations | source inspection, latency не измерена |
| pipeline.plan / pipeline.plan.retry | controller_pipeline.py:1968 | plan happy; truncated retry не измерен |
| pipeline.worker.step_N.iteration.M | controller_pipeline.py:1243 | 3 шага, по 2 main calls |
| pipeline.worker.step_N.answer_auditor | controller_pipeline.py:1304 | 3 auditors |
| pipeline.worker.step_N.answer_repair(.auditor) | controller_pipeline.py:1855 | source inspection, latency не измерена |
| browser_bridge.step_N.answer_auditor / answer_repair(.auditor) | controller_pipeline.py:1304/1855 | source inspection, latency не измерена |
| pipeline.final / pipeline.final.answer_auditor | controller_pipeline.py:2313/2339 | на happy path не вызваны, fallback не измерен |
| voice_brief | voice.py:70/86 | long answer; short и repeat без extra call |

## E. Задержки

Ms; p50 median, p95 nearest-rank (29-я точка из 30). Spans включительные, перекрываются; складывать их в новый total нельзя.

| Сценарий | Этап | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- |
| S1 | request_elapsed_ms | 58.929 | 63.196 | 30 |
| S1 | history_retrieval_ms | 1.978 | 2.862 | 30 |
| S1 | context_preparation_ms | 2.524 | 2.819 | 30 |
| S1 | classification_elapsed_ms | 9.701 | 10.385 | 30 |
| S1 | llm_http_elapsed_ms | 3.691 | 4.338 | 30 |
| S1 | tool_execution_ms | этап отсутствует | этап отсутствует | 0 |
| S1 | auditor_elapsed_ms | 9.668 | 11.008 | 30 |
| S1 | result_persistence_ms | 18.250 | 20.171 | 30 |
| S1 | runtime_message_persistence_ms | 9.109 | 9.972 | 30 |
| S1 | usage_persistence_ms | 26.800 | 28.941 | 30 |
| S1 | polling_total_ms | 16.737 | 18.298 | 30 |
| S1 | payload_measurement_overhead_ms | 1.952 | 2.261 | 30 |
| S3 | request_elapsed_ms | 84.067 | 90.385 | 30 |
| S3 | history_retrieval_ms | 2.021 | 2.861 | 30 |
| S3 | context_preparation_ms | 2.670 | 3.145 | 30 |
| S3 | classification_elapsed_ms | 9.957 | 10.978 | 30 |
| S3 | llm_http_elapsed_ms | 6.966 | 8.710 | 30 |
| S3 | tool_execution_ms | 0.002 | 0.003 | 30 |
| S3 | auditor_elapsed_ms | 9.903 | 10.657 | 30 |
| S3 | result_persistence_ms | 18.779 | 19.599 | 30 |
| S3 | runtime_message_persistence_ms | 9.447 | 9.991 | 30 |
| S3 | usage_persistence_ms | 36.649 | 38.706 | 30 |
| S3 | polling_total_ms | 17.069 | 17.742 | 30 |
| S3 | payload_measurement_overhead_ms | 3.869 | 4.810 | 30 |
| S7 | request_elapsed_ms | 264.251 | 333.610 | 30 |
| S7 | history_retrieval_ms | 1.790 | 2.917 | 30 |
| S7 | context_preparation_ms | 12.211 | 13.296 | 30 |
| S7 | classification_elapsed_ms | этап отсутствует | этап отсутствует | 0 |
| S7 | llm_http_elapsed_ms | 16.315 | 17.981 | 30 |
| S7 | tool_execution_ms | 0.005 | 0.006 | 30 |
| S7 | auditor_elapsed_ms | 29.104 | 31.585 | 30 |
| S7 | result_persistence_ms | 17.614 | 18.523 | 30 |
| S7 | runtime_message_persistence_ms | 8.413 | 9.271 | 30 |
| S7 | usage_persistence_ms | 88.066 | 136.928 | 30 |
| S7 | polling_total_ms | 16.796 | 18.428 | 30 |
| S7 | payload_measurement_overhead_ms | 8.909 | 9.656 | 30 |
| S10_1000x2000 | request_elapsed_ms | 145.452 | 159.264 | 30 |
| S10_1000x2000 | history_retrieval_ms | 1.949 | 3.139 | 30 |
| S10_1000x2000 | context_preparation_ms | 86.551 | 97.058 | 30 |
| S10_1000x2000 | classification_elapsed_ms | 9.893 | 11.330 | 30 |
| S10_1000x2000 | llm_http_elapsed_ms | 3.963 | 5.098 | 30 |
| S10_1000x2000 | tool_execution_ms | этап отсутствует | этап отсутствует | 0 |
| S10_1000x2000 | auditor_elapsed_ms | 9.837 | 11.371 | 30 |
| S10_1000x2000 | result_persistence_ms | 18.721 | 20.644 | 30 |
| S10_1000x2000 | runtime_message_persistence_ms | 9.204 | 10.445 | 30 |
| S10_1000x2000 | usage_persistence_ms | 27.529 | 30.380 | 30 |
| S10_1000x2000 | polling_total_ms | 325.469 | 345.817 | 30 |
| S10_1000x2000 | payload_measurement_overhead_ms | 2.142 | 3.039 | 30 |

| Сценарий | Request p50 | Request p95 | Context p50 | History p50 | Memory block p50 | Poll×5 p50 | n |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S5_50 | 59.740 | 62.982 | 2.625 | 1.951 | 2.202 | 16.537 | 30 |
| S5_500 | 60.828 | 68.224 | 2.673 | 2.593 | 2.250 | 16.523 | 30 |
| S5_5000 | 67.614 | 69.081 | 2.686 | 8.572 | 2.254 | 16.556 | 30 |
| S6_1 | 58.917 | 61.684 | 2.462 | 1.897 | 2.177 | 16.219 | 30 |
| S6_5 | 68.628 | 75.095 | 11.222 | 1.839 | 2.190 | 16.349 | 30 |
| S6_20 | 104.092 | 114.787 | 44.277 | 1.772 | 2.210 | 16.339 | 30 |
| S10_100x100 | 60.649 | 71.589 | 4.260 | 1.769 | 3.922 | 20.123 | 30 |
| S10_1000x100 | 76.932 | 81.706 | 19.740 | 1.886 | 19.428 | 50.904 | 30 |
| S10_1000x2000 | 145.452 | 159.264 | 86.551 | 1.949 | 86.207 | 325.469 | 30 |

Voice: только server post-result functions.

| Сценарий | Этап | p50 ms | p95 ms | n |
| --- | --- | --- | --- | --- |
| S8_short | voice_brief_elapsed_ms | этап отсутствует | этап отсутствует | 0 |
| S8_short | tts_elapsed_ms | 0.272 | 0.482 | 30 |
| S8_long | voice_brief_elapsed_ms | 9.420 | 10.402 | 30 |
| S8_long | tts_elapsed_ms | 0.320 | 0.443 | 30 |

Калибровка recording overhead:

| Сценарий | Recording on p50 | Recording off p50 | Пар |
| --- | --- | --- | --- |
| S1 | 61.323 | 58.737 | 30 |
| S3 | 82.790 | 78.061 | 30 |
| S7 | 262.259 | 251.335 | 30 |
| S10_1000x2000 | 137.941 | 116.417 | 30 |

Recording off сохраняет wrappers и MockTransport; это не pristine A/A. Учёт и сериализация больших builder results добавляют заметный overhead. В mock HTTP входит payload accounting. Эти durations показывают состав/масштабирование работы, но не production SLA; нельзя вычесть percentiles и получить «чистое» время ядра. TTFT/UI delivery/first audio = not_measured. Polling fixture делает пять запросов подряд, без браузерных интервалов. [ui/js/chat.js:905](../../../ui/js/chat.js#L905) подтверждает interval 2000 ms, а не реально измеренную доставку.

## F. Повторная работа и отдельные источники затрат

| Файл/строка | Операция / частота | Объём / время | Возможность устранения |
| --- | --- | --- | --- |
| [server/task_runtime.py:874](../../../server/task_runtime.py#L874); [server/controller.py:443](../../../server/controller.py#L443) | Manifest S1=2, S7=6 | Сам manifest дешёв; связанные profile lookups дороже | Reuse owned snapshot в пределах запроса |
| [server/device_context.py:156](../../../server/device_context.py#L156) | Runtime profile lookups для 1/5/20 devices: 2/10/40 | Context p50 2.5→11.2→44.3 ms | Не читать профили дважды, сохранить ownership/freshness |
| [server/controller.py:717](../../../server/controller.py#L717); [server/controller_pipeline.py:1919](../../../server/controller_pipeline.py#L1919) | Eager runtime до route, затем PLAN shared/workers | S7 memory builder 5, artifact builder 2 | Убрать pre-route повторную подготовку |
| [server/controller_shared.py:405](../../../server/controller_shared.py#L405); [server/database.py:1057](../../../server/database.py#L1057) | Все facts до cap 2048 chars; повторный assemble кандидатов | 1000 больших facts → emitted 177 chars; context p50 86.55 ms | Оптимизировать выбор, не менять order/skip/dedup/isolation |
| [server/routers/tasks.py:441](../../../server/routers/tasks.py#L441); [server/routers/tasks.py:447](../../../server/routers/tasks.py#L447) | 5 polls → 10 full facts reads + 5 profile reads | S10 10000 fact rows; poll×5 p50 325.47 ms | Не читать дважды и не возвращать все facts каждый tick |
| [server/controller_non_pipeline.py:373](../../../server/controller_non_pipeline.py#L373) | System/schemas/history/tool results повторяются каждый turn | 39 schemas = 25895 bytes/обычный main | Сначала измерить cache; не убирать tools по словам |
| [server/controller_non_pipeline.py:433](../../../server/controller_non_pipeline.py#L433); [server/controller_non_pipeline.py:1268](../../../server/controller_non_pipeline.py#L1268) | Forced raw-only loop → 20 main + repair/auditor/classifier | 23 calls, ~1.239 MB input | Раньше bounded repair при доказанном no-progress |
| [server/answer_auditor.py:60](../../../server/answer_auditor.py#L60) | S1=1; S7=3; rejection=2 | S1 auditor input 1058 bytes | Сохранить auditor, оценить compact journal |
| [server/voice.py:98](../../../server/voice.py#L98) | Short brief=0, long=1; повтор кэширован | 30/30 cache checks | Дублирование brief не подтверждено |

Runtime memory reads S1=1, S7=5; дополнительно polling=10. Runtime profile reads S1=2/S3=3/S7=7, дополнительно polling=5. S10: runtime fact text 2016890 bytes, polling 20168900 bytes; один response 2077521–2077521 bytes, пять 10387605 bytes. Это serialized fixture response до compression, не замер трафика телефона.

История 50/500/5000: runtime получает limit50, main содержит 49 previous + current + system = 51 messages; input ~82.1→82.2 KB, не рост в 100 раз. Разница — длина fixture индексов. В данном пути нет compression, есть count truncation. [server/database.py:496](../../../server/database.py#L496) и [server/database.py:128](../../../server/database.py#L128): index chat_id, сортировка created_at. Локальный рост SQL read не доказывает необходимость migration без production query plan.

Python probe: check вызывается один раз в ordinary path, но эти запросы early-return; дополнительных agent scans нет. Лишние Python runtime probes не доказаны; Python/pip/fresh-stale fixtures и реальный discovery не измерены. [server/task_runtime.py:213](../../../server/task_runtime.py#L213)

Затраты по независимым осям:

- Локальное время: большая память и full polling, рост inventory lookups. SQLite usage/message commits заметны без реальной сети; их первенство в production не доказано.
- Input tokens: неизвестны; измеренные bytes концентрируются в system/schemas и повторениях. Cache может менять оплату.
- Calls: обычный разговор 3, action4, PLAN10, forced raw failure23, long voice +1 после результата. Classifier и auditor решают разные задачи.
- SQL/локальные операции: profile reads масштабируются с devices, память materialize целиком, polling читает дважды.
- Tool RTT: S3=1, S7=3, execution recovery=2; fake latency почти нулевая. Настоящий WebSocket RTT неизвестен; correction LLM call не равен device round trip.


## G. Приоритетные предложения — пять, не применены

1. **P1 — polling memory.** tasks.py:441–447 и existing memory API/UI consumer. Убрать redundant первый full read при owned profile; полный facts_list получать по необходимости через existing endpoint, совместимость payload/UI согласовать. Экономия DB/materialization/serialization/traffic. Риск stale facts и device legacy isolation. До/после: одинаковые fixtures, reads/rows/bytes/p50/p95 и production poll. Tests: offline/account/device facts, deletion/write refresh, owner isolation, admin/non-admin payload.

2. **P1 — pre-route repeated context.** runtime.py:874, controller.py:427/717, device_context.py:156, pipeline.py:1919. Передавать уже подготовленные owned profiles/manifest через существующие kwargs/shared structures. Без нового Context Engine/Request Context и межзапросного cache. Экономия repeated builds/lookups; особенно много devices/PLAN. Риск stale activation/runtime и смешение target/user. До/после: 1/5/20 devices и PLAN counts + payload equivalence. Tests: одинаковый short device_id разных users, explicit invalid target, stale receipts, tool refresh, handoff.

3. **P1 — bounded memory selection.** shared.py:405/database.py:1057. Рассмотреть page/stream iteration и расчёт serialized record size вместо многократного assemble; сохранить valid JSON, порядок, oversized skipping, omission counts, dedup и command memory. Первые N facts брать нельзя: большой ранний факт не должен вытеснять подходящий поздний. Экономия локальной сериализации/материализации, SQL эффект неизвестен. Риск смены selection/isolation. До/после: те же sizes, emitted JSON и counts. Tests: giant-before-short, Unicode/escape, empty memory, owner/device scope, cap/authority.

4. **P1 — bounded repair при повторном protocol failure.** non_pipeline.py:433/1268 и существующий answer_repair. После нескольких эквивалентных безрезультатных raw/invalid responses переходить к имеющемуся answer-only repair или честной failure. Не повторять actions и не обходить auditor/basis. Экономия main calls/repeated prompts. Риск преждевременного отказа. До/после: identical raw failures, разные corrections, useful progress/evidence и auditor rejection. Tests: one repair, one tool/turn, current basis, no false success, cancel/confirm/recovery.

5. **P2 — эксперимент с classification round trip.** classify_task_complexity и существующий основной выбор. Проверить возможность route decision основной моделью без отдельного classifier; сначала ограниченный эксперимент, не production patch. Измерено +1 call, но только 655–709 input bytes. Нельзя заменять intent regex/словами или отключать PLAN suggestion/review. Экономия сетевого round trip; эффект неизвестен. Риск wrong routing/раннего действия. До/после: SIMPLE/PLAN/ambiguous corpus, wrong-route rate, реальные calls/latency/usage. Tests: routing parity, declined plan/review, no-device, multi-device/multi-action safety. Classifier пока сохраняется.

Schemas: 39 инструментов/25.9 KB подтверждены. Сокращение descriptions/registry допустимо обсуждать после provider usage/cache и tests tool availability. Уменьшать toolset по отсутствию слов «браузер»/«файл» нельзя; нового judge/router не предлагается. Процент ускорения по bytes выдумывать нельзя.

Tool results S3 повторяются в следующем main и auditor journal: это обоснованная evidence передача, fixture объём мал. Удалять без сохранения basis нельзя. Auditor во всех предложениях сохраняется; его отключение не является оптимизацией.


## H. Риски и регрессии

Fake success доказывает controller path, не создание реальной папки. Настоящая LLM может выбирать другие tools, confirmation и iterations. На VPS network/storage/load другие. Inclusive spans пересекаются: auditor включает completion+usage, context включает memory/manifest; SQL helper sums включают post-result polls. Накладные расходы учёта отдельно показаны.

Сохранять ownership/device paths, one tool per iteration, terminal answer/basis, cancel/idempotent confirm, PLAN review/recovery, memory/page data-only boundaries, DeepTalk/voice cache. Instrumentation должна быть failure-isolated и не логировать contents. Missing tokens/cost не считать нулями.

## I. Тесты и команды

Related IRU tests: **158 passed**, 1 Starlette deprecation warning, 21.03 s. External baseline/proposal tests: **33 passed**, 0.09 s. Проверены 21 сценарий, 30 samples/p95, bytes accounting, dispatch counts, unknown usage, head/isolation, history cap, profiles/PLAN/handoff/polling/memory/voice, privacy. Proposal tests: default-off, numeric metadata, missing vs zero usage, HTTP exception/counter и equivalence classifier payload AST. Compile проверен для harness и двух preview sources. Git diff --check exit0; tracked diff пуст. Full pytest **не запускался** в этой задаче.

В разработке harness были ошибки импортов/synthetic answer_type и JSON serialization callable в route kwargs; первоначальная assertion проверка 25 failed/8 passed. После исправления были неверные assumptions о integer→string sanitizer и aggregate SQL rows (2 failed/31 passed). Это fixture/measurement ошибки, не найденные regressions IRU. Затронутые measurements отброшены, пересняты с worker/terminal assertions; tool correction использует отсутствие required command.

PowerShell setup:

```powershell
$repoPath = 'C:\Users\russa\OneDrive\Desktop\ИРУ\IRU'
$artifactPath = 'C:\Users\russa\.codex\visualizations\2026\09\22\01a0ca0f-0e25-7792-aecd-e94d69452f54'
$py = 'C:/Users/russa/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$env:PYTHONPATH = "$env:TEMP/iru-audit-20261002/deps;$repoPath"
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $repoPath
```

Фактически выполненные relevant tests:

```powershell
& $py -m pytest -q tests/test_llm_usage.py tests/test_request_lifecycle_trace.py tests/test_lazy_context.py tests/test_controller_pipeline_budget.py tests/test_pipeline_planner_budget.py tests/test_voice.py tests/test_voice_brief.py tests/test_memory_authority_regressions.py tests/test_confirmed_command_outcome.py --basetemp "$env:TEMP/iru-p003-tests-20261008"
```

Измерения и отдельный повтор S9:

```powershell
& $py "$artifactPath/iru_p0_03_baseline.py" --samples 30 --calibrate --output "$artifactPath/IRU-P0-03-metrics.json"
& $py "$artifactPath/iru_p0_03_baseline.py" --samples 30 --scenario S9_tool_correction --output "$artifactPath/p0-03-tool-correction.json"
& $py "$artifactPath/merge_measurements.py"
& $py -m pytest -q "$artifactPath/test_p0_03_baseline.py" --basetemp "$env:TEMP/iru-p003-final-validation-20261008"
git diff --check
git diff --stat a55512b2e21a6448689bbdeda4d35ff6b4136589
git status --short --untracked-files=no
```

Нужен указанный runtime/test dependencies. Harness имеет REPO константу фактического checkout; для другого окружения меняется только внешняя константа. Запускать отдельным дочерним Python процессом: MockTransport/DB_PATH replacements не предназначены для production server процесса.

## J. Пробелы измерений

1. Реальные tokens/reasoning/cache/cost: provider usage отсутствует в mock, existing zero DB fields не используются как данные.
2. Production LLM latency/network RTT/load/queue/fsync/retry частота: live вызовы не разрешены; mock latency не подменяет production.
3. TTFT: non-streaming response, измерим только полный HTTP completion.
4. Browser STT/wake word/UI delivery/first audio: реального browser/audio не было. S8 только server post-result path.
5. Полный fixed/dynamic split unmatched system residual: нужны caller-level tags. Whole input byte/char accounting точен, остаток не называется exclusively fixed.
6. Качество LLM/planning и полезность фактов: ответы fixture; S4 наличие artifact context не доказывает его использование.
7. Не измерены latency альтернативных onboarding/autonomous/broadcast/window/browser/memory-only routes; PLAN truncation/retry/final fallback; human confirmation wait; HTTP retries/auditor malformed JSON retry. Они существуют, расходы не считаются нулевыми.
8. Реальный Python discovery/receipt freshness, filesystem/Yandex search/browser calls не исполнялись.
9. Cache-hit экономия и compression не измерены; transferred bytes не равны оплаченной cache-miss работе.
10. Sequential fixtures не моделируют production concurrency, disconnect/cancel races и background load.

Это локальная воспроизводимая база, не production SLO. Следующий этап — opt-in existing telemetry и sampling настоящих runs владельцем.

## K. Следующий minimal patch и отдельный diff

**Не применён:** `IRU-P0-03-proposed-instrumentation.diff`, создан из текущего HEAD вне checkout.

Первый предлагаемый patch:

| Файл | Только instrumentation |
| --- | --- |
| server/llm_usage.py | opt-in IRU_PERFORMANCE_BASELINE=1; input bytes/chars/schema/role counters, HTTP attempts/durations, usage-known flags в existing metadata.performance; default-off/failure isolation |
| server/controller.py | hooks существующего classifier и общего completion HTTP, включая required→auto fallback; модели/prompts/tools/loops/retries не меняются |

Это первый core HTTP/input diff, не полный stage/context instrumentation. Новые performance поля числовые; payload/paths/headers/secrets не сохраняются. Monotonic start остаётся только scoped context. Старые token columns совместимы; при *_known=0 аналитика обязана показывать unknown, не считать существующий zero cost доказанным. Переход legacy accounting на nullable columns/migrations не предлагается.

Дальнейшие точные hooks требуют отдельного согласования, полного diff для них пока нет:

- controller_onboarding.py:70 — отдельный direct HTTP accounting для onboarding; первый core diff этот путь пока не покрывает.
- run_journal.py:34 — numeric event/stage allowlist, существующие task_id/cap/failure isolation.
- task_runtime.py:864/874/1087/1303 — history/context/tool await/final persistence durations и dispatch count.
- controller.py:427/pipeline.py:579/650 — caller-defined sizes memory/device/artifact/history/handoff, без contents.
- answer_auditor.py:60/voice.py:70/120 — elapsed stages поверх существующих HTTP metadata, без дополнительных calls.
- routers/tasks.py:435 — polling duration/rows/response numeric size, owner auth и без periodic worker.

Перед применением требуется review и отдельное разрешение согласно §7 задания. После разрешения: enabled/disabled equivalence, retry/missing usage/privacy tests, cancel/confirmation tests, relevant/full suite. Затем production sampling владельцем; оптимизация только последующим согласованным этапом. Без UI instrumentation first_audio остаётся not_measured.

После отчёта работа остановлена. Commit/push/PR нет.
