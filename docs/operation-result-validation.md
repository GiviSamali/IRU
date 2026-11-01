# Проверка пакета перед публикацией

База: `3d8e2dc`. Владелец отдельно разрешил локальные тесты и commit/push в main. Развёртывание не разрешалось и не выполнялось.

## Адресный дефект runtime_summary

Обработка явных `failed`, `unknown`, отрицательного completion_state выполняется раньше ветки совместимости runtime_summary. Совместимость ограничена известными статусами runtime; наличие summary само по себе не является успехом. Для unknown чтения сохраняется принятая политика failed-наблюдения без блокировки мутаций.

Добавлены 9 случаев: два отрицательных status × три summary, плюс три completion_state. Все прошли.

## Результаты

- Основной набор: **174 passed** на окончательном варианте. Первый запуск выявил ошибку fake-фикстуры news (worker_id без user_id/chat_id для persistence); исправлена именно изоляция unit-сценария.
- Защитный набор: **206 passed, 1 failed**. На чистой базе тех же тестов: **205 passed, то же 1 failed**; текущий набор содержит новый тест передачи. Падение — старый preview-only адаптер `test_simple_model_loop_keeps_journal_without_extra_model_calls[Remove-Item report.txt-False-True]`.
- После финального уточнения проекции квитанций отдельно повторены transfer/multi-device: **49 passed**.
- Расширенные старые интеграционные сценарии: **72 passed, 29 failed**, против **77 passed, 24 failed** на базе в том же окружении. Это НЕ зелёный набор. Он запускался до последнего восстановления unknown для незавершённой CLI-мутации.
- После этого адресно повторены четыре негативных сценария: **3 passed, 1 failed**; основной набор 174 снова прошёл. Остался `test_action_proof_cannot_override_negative_or_uncertain_execution[result2]`: failed patch, затем успешная непрозрачная verification-команда и утверждение Worker о полном результате. Код не может доказать, что второй произвольный скрипт только читает, а не исправляет. Это конкретная граница принятого доверия Worker, не переопределение неудачи первого инструмента и не случай runtime_summary.

Не переписаны массово старые ожидания Assessor/частичности ради зелёного результата. Полный репозиторный suite не запускался. Стоимость/задержка настоящей модели и LIVE не измерялись.

## Сравнение расширенного набора

Совпадающие падения: 9. Старые падения, исчезнувшие в этом запуске: 15. Новые несовпадающие ожидания: 20. Среди новых — отменённая автоматическая семантическая оценка, изменённые статусы уточнений/подтверждений и старые контракты полноты; нельзя автоматически объявлять все несовпадения безопасными регрессиями или все — дефектами исполнения. Два новых отрицательных случая обнаружили необходимость сохранить блокировку незавершённой мутации; started теперь снова unknown, отдельная перепроверка это подтверждает.

Список новых несовпадений этого расширенного запуска:

- `tests.test_experiment_recovery::test_action_proof_cannot_override_negative_or_uncertain_execution[result0]`
- `tests.test_experiment_recovery::test_action_proof_cannot_override_negative_or_uncertain_execution[result2]`
- `tests.test_experiment_recovery::test_bare_auditor_acceptance_cannot_confirm_action[reply0]`
- `tests.test_experiment_recovery::test_bare_auditor_acceptance_cannot_confirm_action[reply1]`
- `tests.test_experiment_recovery::test_bare_auditor_acceptance_cannot_confirm_action[reply2]`
- `tests.test_experiment_recovery::test_bare_auditor_acceptance_cannot_confirm_action[reply3]`
- `tests.test_experiment_recovery::test_bare_auditor_acceptance_cannot_confirm_action[reply4]`
- `tests.test_experiment_recovery::test_internal_memory_proposal_is_normalized_before_audit_and_never_written_automatically`
- `tests.test_experiment_recovery::test_markerless_action_uses_bound_existing_audit_and_survives_restore[COMPILE_OK]`
- `tests.test_experiment_recovery::test_markerless_action_uses_bound_existing_audit_and_survives_restore[EDIT OK bytes=12869]`
- `tests.test_experiment_recovery::test_markerless_action_uses_bound_existing_audit_and_survives_restore[OK backup: control.bak]`
- `tests.test_experiment_recovery::test_markerless_action_uses_bound_existing_audit_and_survives_restore[OK replacements: [1]]`
- `tests.test_experiment_recovery::test_markerless_action_uses_bound_existing_audit_and_survives_restore[STEP_A_OK]`
- `tests.test_experiment_recovery::test_markerless_action_uses_bound_existing_audit_and_survives_restore[\u043a\u043e\u043c\u043f\u0438\u043b\u044f\u0446\u0438\u044f: OK]`
- `tests.test_experiment_recovery::test_ordinary_confirmation_retains_controller_and_does_not_repeat_completed_step[concurrent]`
- `tests.test_experiment_recovery::test_ordinary_confirmation_retains_controller_and_does_not_repeat_completed_step[confirm]`
- `tests.test_experiment_recovery::test_real_clarification_is_presented_as_question_not_unknown`
- `tests.test_worker_execution_simplification::test_cli_office_receipt_and_stdout_cannot_hide_missing_content_pages`
- `tests.test_worker_execution_simplification::test_find_pptx_conditional_cli_open_or_list[1-False]`
- `tests.test_worker_execution_simplification::test_verified_text_bytes_do_not_override_explicit_missing_content`

Журналы/JUnit и подробная классификация сохранены локально в `notes/operation-result-contract-20261101`. Сервер, production-БД и установленный Agent не менялись.
